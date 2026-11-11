"""Independent artifact/source/log audit of completed vector candidate stages.

Missing stages stay pending. This audit never accepts organizer accuracy or
final ADP and does not convert a mapped timing failure into a pass.
"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BASE = ROOT.parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--revision', required=True)
args = parser.parse_args()
assert re.fullmatch(r'[A-Za-z0-9_-]+', args.revision)
case = ROOT / ('work/vecpipe-evidence-review-' + args.revision)
assert not case.exists(), 'Preserve prior review; use a new revision'


def local(name):
    for prefix in ['/workspace/', '/mnt/data/Ubuntu/qingruan/']:
        if name.startswith(prefix):
            return BASE / name[len(prefix):]
    return Path(name)


def sha(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


guarded = {str(Path(__file__)): sha(Path(__file__))}


def checked(path, statuses):
    data = json.loads(path.read_text())
    assert data['status'] in statuses, (str(path), data['status'])
    guarded[str(path)] = sha(path)
    for name, expected in data.get('source_sha256', {}).items():
        p = local(name)
        assert sha(p) == expected, name
        guarded[str(p)] = expected
    for name, expected in data.get('artifact_sha256', {}).items():
        p = path.parent / name
        assert sha(p) == expected, str(p)
        guarded[str(p)] = expected
    if 'simulator_sha256' in data:
        assert sha(path.parent / 'simv') == data['simulator_sha256']
        guarded[str(path.parent / 'simv')] = data['simulator_sha256']
    return data


def clean(text):
    assert not re.search(r'Fatal:|Error-\[|VECPIPE_(?:FAIL|FIFO_FAIL|AXI_FAIL|SHARED_FAIL)|AXI_VECTOR_FAIL', text)


arithmetic = checked(ROOT / 'work/vecpipe-arithmetic-v1/summary.json',
    ['VECPIPE_POINTWISE_BITS_FLAGS_ORDERING_AND_ONE_PER_CYCLE_POST_UNIT_PASS'])
assert arithmetic['cases'] == 148987 and arithmetic['all_S8_U8_byte_zero_pairs_checked'] == 131072
log = (ROOT / 'work/vecpipe-arithmetic-v1/normal.log').read_text()
clean(log)
assert log.count('VECPIPE_ARITHMETIC_PASS cases=148987 exact_bits_errors_flags=1 randomized_output_stalls=1 mixed_DIV_ordering=1 POST_256_outputs_one_per_cycle=1') == 1
protocol = checked(ROOT / 'work/vecpipe-protocol-v1/summary.json',
    ['VECPIPE_INFLIGHT_CANCEL_VISIBLE_RESULT_AND_RESET_PROTOCOL_UNIT_PASS'])
log = (ROOT / 'work/vecpipe-protocol-v1/normal.log').read_text()
clean(log)
assert log.count('VECPIPE_PROTOCOL_PASS checked=12') == 1
axi = checked(ROOT / 'work/vecpipe-axi-v1/summary.json',
    ['VECPIPE_AXI_REFERENCE_FAULT_CANCEL_RESET_THREE_SRAM_GEOMETRIES_PASS'])
comparisons = []
for banks in [128, 256, 512]:
    scalar = next(item for item in axi['results'] if item['mode'] == 'scalar' and item['banks'] == banks)
    pipeline = next(item for item in axi['results'] if item['mode'] == 'pipeline' and item['banks'] == banks)
    assert len(scalar['cases']) == 214 and len(pipeline['cases']) == 220
    for mode, result in [('scalar', scalar), ('pipeline', pipeline)]:
        directory = ROOT / ('work/vecpipe-axi-v1/' + mode + '_' + str(banks))
        assert sha(directory / 'simv') == result['simulator_sha256']
        log = (directory / 'normal.log').read_text()
        clean(log)
        assert log.count(result['marker']) == 1
        cases = [dict(zip(['case', 'op', 'cycles', 'bytes', 'error'], map(int, match.groups())))
                 for match in re.finditer(r'VECPIPE_AXI_CASE case=(\d+) op=(\d+) cycles=(\d+) bytes=(\d+) error=(\d+)', log)]
        assert cases == result['cases']
        guarded[str(directory / 'normal.log')] = sha(directory / 'normal.log')
        guarded[str(directory / 'simv')] = result['simulator_sha256']
    assert all(item['error'] == 0 for item in scalar['cases'][:120] + pipeline['cases'][:120])
    before = sum(item['cycles'] for item in scalar['cases'][:120])
    after = sum(item['cycles'] for item in pipeline['cases'][:120])
    expected = next(item for item in axi['comparisons'] if item['banks'] == banks)
    assert expected['scalar_reference_cycles'] == before and expected['pipeline_reference_cycles'] == after
    assert expected['reference_workload_speedup'] == before / after
    comparisons.append(expected)
shared = checked(ROOT / 'work/vecpipe-shared-axi-v1/summary.json',
    ['VECPIPE_COMPLETE_SHARED_BACKEND_AXI_FAULT_CANCEL_RESET_THREE_GEOMETRIES_PASS'])
for item in shared['results']:
    directory = ROOT / ('work/vecpipe-shared-axi-v1/' + str(item['banks']))
    assert item['commands'] == 220 and item['engine_cycles_byte_equal_to_isolated_pipeline']
    assert sha(directory / 'simv') == item['simulator_sha256']
    log = (directory / 'normal.log').read_text()
    clean(log)
    assert log.count(item['marker']) == 1 and log.count('VECPIPE_AXI_EXTRA_PASS fast_cancel=2 reset=2') == 1
    actual = [tuple(map(int, match.groups())) for match in re.finditer(r'VECPIPE_AXI_CASE case=(\d+) op=(\d+) cycles=(\d+) bytes=(\d+) error=(\d+)', log)]
    isolated = next(result for result in axi['results'] if result['mode'] == 'pipeline' and result['banks'] == item['banks'])
    assert actual == [tuple(result[key] for key in ['case', 'op', 'cycles', 'bytes', 'error']) for result in isolated['cases']]
    guarded[str(directory / 'normal.log')] = sha(directory / 'normal.log')
    guarded[str(directory / 'simv')] = item['simulator_sha256']
backend = (ROOT / 'rtl/axi_accel_shared_backend.sv').read_text()
expected_backend = backend.replace('module axi_accel_backend #(', 'module axi_accel_backend_vecpipe #(').replace('axi_vector_engine #(', 'axi_vector_engine_vecpipe #(')
assert expected_backend == (HERE / 'axi_accel_backend_vecpipe.sv').read_text()
stages = {'arithmetic': arithmetic['status'], 'inflight_protocol': protocol['status'],
          'isolated_AXI_three_geometries': axi['status'], 'shared_backend_AXI_three_geometries': shared['status']}
pending = []
for label, directory, statuses in [
    ('RV32_all8_micrograph', 'vecpipe-soc-v1', ['VECPIPE_REAL_RV32_ALL8_SHARED_BACKEND_FROZEN_FIRMWARE_AND_CPU_NEGATIVE_PASS']),
    ('complete_first_image', 'vecpipe-full-rv32-v1', ['VECPIPE_REAL_RV32_FULL_FIRST_IMAGE_BYTE_EXACT_PASS_OFFICIAL_ACCURACY_OPEN']),
    ('mapped_backend', 'vecpipe-backend-synthesis-v1', ['VECPIPE_BACKEND_MAPPED_SETUP_PASS', 'VECPIPE_BACKEND_MAPPED_SETUP_FAIL']),
    ('mapped_complete_SoC', 'vecpipe-full-soc-synthesis-v1', ['VECPIPE_FULL_ALL8_SOC_MAPPED_TIMING_PASS', 'VECPIPE_FULL_ALL8_SOC_MAPPED_TIMING_FAIL'])]:
    path = ROOT / 'work' / directory / 'summary.json'
    if path.exists():
        data = checked(path, statuses)
        stages[label] = data['status']
        if data['status'].endswith('_FAIL'):
            pending.append(label + ': timing failed; closure required')
        if label == 'complete_first_image':
            assert data['logits_byte_exact_to_completed_scalar_and_frozen_C']
            assert sha(path.parent / 'logits.bin') == data['logits_sha256']
    else:
        pending.append(label + ': no completed proof')
case.mkdir()
shutil.copyfile(Path(__file__), case / 'review_driver.py')
summary = {'status': 'VECPIPE_COMPLETED_STAGE_EVIDENCE_SOURCE_AND_LOG_AUDIT_PASS_FULL_ACCEPTANCE_PENDING',
           'observed_at': datetime.now().astimezone().isoformat(), 'stages': stages,
           'comparisons': comparisons, 'pending': pending + ['final whole-SoC timing/gate evidence', 'official reference accuracy', 'final fair ADP selection/delivery'],
           'source_sha256': guarded, 'official_accuracy_accepted': False, 'final_ADP_accepted': False,
           'scope': 'Independent source/artifact/log and identical reference-workload audit. Missing results and timing failures remain explicit; local vector workload speedup is not full-model speedup.'}
(case / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
print(json.dumps({key: summary[key] for key in ['status', 'stages', 'pending']}, indent=2), flush=True)
