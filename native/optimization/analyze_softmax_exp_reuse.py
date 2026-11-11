"""Read-only graph/domain and exp-input reuse analysis; no accelerator changes.

Captured operands cover two Softmax nodes of one private image, not20 images.
Cache hit counts are workload observations, never hardware speed measurements.
"""
import argparse
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import numpy as np

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--revision', required=True)
args = parser.parse_args()
assert args.revision.replace('_', '').replace('-', '').isalnum()
OUT = ROOT / ('work/softmax-exp-reuse-' + args.revision)
assert not OUT.exists(), 'Preserve prior evidence'
if 23 in os.sched_getaffinity(0):
    os.sched_setaffinity(0, {23})  # Only this short analysis child, not any simulator.
assert ctypes.CDLL('libm.so.6').fegetround() == 0
hashes = {}


def read(path):
    data = path.read_bytes()
    hashes[str(path)] = hashlib.sha256(data).hexdigest()
    return data


replay = ROOT / 'work/conversion-replay-relocation-v1'
plan = json.loads(read(replay / 'expected/base-plan.json'))
compiled = replay / 'native/swin-tiny/work/verify.ylnpqxth'
manifest = json.loads(read(compiled / 'graph_manifest.json'))
constants = read(compiled / 'constants.bin')
assert hashlib.sha256(constants).hexdigest() == manifest['constant_sha256']
assert plan['model_sha256'] == manifest['model_sha256']
tensors = {item['id']: item for item in plan['tensors']}
nodes = {item['node_index']: item for item in plan['schedule']}
producers = {output: node for node in plan['schedule'] for output in node['outputs']}
domains = []
for node in plan['schedule']:
    if node['op'] != 'Softmax':
        continue
    current = node['inputs'][0]
    chain = []
    additions = []
    for _ in range(12):
        source = producers[current]
        chain.append({'node': source['node_index'], 'op': source['op'], 'inputs': source['inputs']})
        if source['op'] == 'DequantizeLinear':
            break
        assert source['op'] in ['Add', 'Reshape', 'Identity']
        if source['op'] == 'Add':
            additions.append(source)
        current = source['inputs'][0]
    assert chain[-1]['op'] == 'DequantizeLinear' and additions
    bias = tensors[additions[-1]['inputs'][1]]
    assert bias['storage'] == 'constant' and bias['dtype'] == 'float32'
    channels = tensors[node['inputs'][0]]['shape'][-1]
    assert channels == 49
    first_row = struct.unpack_from('<49f', constants, bias['offset'])
    assert all(math.isfinite(value) for value in first_row)
    assert max(first_row) - min(first_row) > 0.001
    domains.append({'Softmax_node': node['node_index'], 'name': node['name'],
        'input_shape': tensors[node['inputs'][0]]['shape'], 'upstream_chain': chain,
        'FP32_Adds_after_last_DQ': len(additions), 'relative_bias_tensor': bias['id'],
        'first_bias_row_distinct_values': len(set(first_row)),
        'first_bias_row_min': min(first_row), 'first_bias_row_max': max(first_row)})
assert len(domains) == 12

captured = ROOT / 'work/newlib-difference'
prior = json.loads(read(captured / 'summary.json'))
assert prior['image'] == 'ILSVRC2012_val_00000759.JPEG' and prior['its_input_byte_identical']
results = []
for index in [480, 557]:
    raw = read(captured / 'glibc' / ('node_%d_input.bin' % index))
    assert raw == read(captured / 'newlib' / ('node_%d_input.bin' % index))
    shape = tensors[nodes[index]['inputs'][0]]['shape']
    assert len(raw) == math.prod(shape) * 4
    values = np.frombuffer(raw, dtype='<f4').reshape(-1, 49)
    assert np.isfinite(values).all()
    maximum = values.max(axis=1, keepdims=True)
    differences = np.subtract(values, maximum, dtype=np.float32)
    assert (differences <= 0).all()
    nonzero = np.abs(differences[differences != 0])
    assert not len(nonzero) or nonzero.min() >= np.finfo(np.float32).tiny
    keys = differences.astype('<f4', copy=False).view('<u4').reshape(-1).tolist()
    eligible = [bool(key & 0x80000000) and 0x34000000 <= (key & 0x7fffffff) <= 0x42cff1b5 for key in keys]
    variants = []
    for flush_each_row in [False, True]:
        for entries in [16, 32, 64, 128]:
            assert 49 + 2 * entries <= 128 * 4
            tags = [-1] * entries
            hits = misses = 0
            for position, (key, candidate) in enumerate(zip(keys, eligible)):
                if flush_each_row and position % 49 == 0:
                    tags = [-1] * entries
                if not candidate:
                    continue
                slot = (key ^ (key >> 7) ^ (key >> 14) ^ (key >> 21) ^ (key >> 28)) & (entries - 1)
                if tags[slot] == key:
                    hits += 1
                else:
                    misses += 1
                    tags[slot] = key
            variants.append({'entries': entries, 'flush_scope': 'row' if flush_each_row else 'external_Softmax_command',
                'hypothetical_key_value_bytes': 8 * entries, 'row_plus_cache_bytes': 49 * 4 + entries * 8,
                'PE_existing_cache_bytes_R8_C16': 128 * 16, 'hits': hits, 'misses': misses,
                'eligible_hit_fraction': hits / max(hits + misses, 1), 'all_values_hit_fraction': hits / len(keys)})
    results.append({'Softmax_node': index, 'rows': len(values), 'channels': 49, 'values': len(keys),
        'eligible_values': sum(eligible), 'bypassed_fast_or_special_values': len(keys) - sum(eligible),
        'distinct_difference_FP32_bits': len(set(keys)), 'variants': variants})

for path in [Path(__file__), ROOT / 'rtl/fp32_exp.sv', ROOT / 'rtl/fp32_softmax_row.sv']:
    read(path)
OUT.mkdir()
summary = {'status': 'STATIC_SOFTMAX_FP32_BIAS_DOMAIN_AND_CAPTURED_EXP_INPUT_REUSE_ANALYSIS_COMPLETE',
    'observed_at': datetime.now(timezone.utc).isoformat(), 'numpy_version': np.__version__,
    'rounding_mode': 'FE_TONEAREST; finite FP32 subtraction, no nonzero subnormal differences',
    'model_sha256': plan['model_sha256'], 'Softmax_nodes': domains,
    'simple_INT8_difference_only_LUT_not_justified': True,
    'reason': 'All12 nodes have FP32 bias additions after DQ. Equal quantized codes can give different per-key scores; shifted blocks have another Add. Max/sub/exp cannot be indexed solely by an INT8 code difference while ignoring these additions.',
    'captured_image': prior['image'], 'captured_profiles': results,
    'cache_hypothesis': 'Memoize exact FP32 exp input bits/results inside currently unused PE SRAM slots; filter cheap/special exp cases; invalidate at external command/cancel/ownership change. No such RTL is implemented by this analysis.',
    'source_sha256': hashes, 'model_preprocessing_firmware_or_RTL_modified': False,
    'exp_output_bits_or_flags_verified_by_this_analysis': False,
    'new_cache_RTL_implemented': False, 'hardware_latency_or_area_measured': False,
    'dataset20_profile_complete': False, 'official_accuracy_accepted': False,
    'scope': 'Static12-node domain plus two already-captured finite Softmax inputs of one private image. Simulated hit counts and byte capacity are not feasibility, functional, PPA or whole-model performance acceptance.'}
(OUT / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
print(summary['status'])
for item in results:
    print(json.dumps({'node': item['Softmax_node'], 'values': item['values'], 'eligible': item['eligible_values'],
                      'variants': item['variants']}, indent=2))
