"""Plan hardware-streamable GELU fusion and contiguous aliases together.

All lifetimes refer to the actual fused execution events. In particular, the
raw input and output of a fusion must occupy disjoint physical allocations.
The original descriptor ABI is retained, with reserved[0] bit 0 declaring that
the fusion manifest is mandatory. Eliminated temporaries are never executed.
"""
import collections
import hashlib
import json
import shutil
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parents[1]
sys.path[:0] = [str(BASE / 'native/swin-tiny'), str(BASE / 'native/swin-tiny/runtime')]
from swin.compile import emit

source = ROOT / 'work/view-plan'
tables = ROOT / 'work/lookup-tables'
case = ROOT / 'work/fused-plan'
case.mkdir(parents=True, exist_ok=True)
plan = json.loads((source / 'plan.json').read_text())
tensors, nodes = plan['tensors'], plan['schedule']
fusions = json.loads((tables / 'gelu-fusions.json').read_text())
starts = {f['start']: f for f in fusions}
skipped, eliminated, events = set(), set(), []

def physical(i):
    t = tensors[i]
    if 'view_of' in t:
        return physical(t['view_of'])
    if t['storage'] == 'quantized_view':
        return physical(t['raw_tensor'])
    return i

for f in fusions:
    assert f['start'] <= f['end'] < len(nodes)
    for n in nodes[f['start']:f['end'] + 1]:
        assert n['node_index'] not in skipped
        skipped.add(n['node_index'])
        eliminated.update(i for i in n['outputs'] if i != f['output'])
    assert tensors[f['raw_input']]['bytes'] == tensors[f['output']]['bytes'] == f['elements']
    assert f['elements'] % f['channels'] == 0

for n in nodes:
    step = n['node_index']
    if step in starts:
        f = starts[step]
        events.append((step, [f['raw_input']], [f['output']], 'gelu'))
    elif step not in skipped:
        assert not any(i in eliminated for i in n['inputs']), n
        inputs = list(n['inputs'])
        if n['executor'] == 'pe':
            inputs += [n['job'][p]['tensor_id'] for p in ['A', 'B']]
        events.append((step, inputs, n['outputs'], n['op']))

last, birth = {}, {}
for step, inputs, outputs, _ in events:
    for i in inputs:
        owner = physical(i)
        last[owner] = max(last.get(owner, -1), step)
    for i in outputs:
        if physical(i) == i:
            birth[i] = step
for i, t in enumerate(tensors):
    if t['name'] == 'input':
        birth[i] = -1
    if t['name'] == 'output':
        last[physical(i)] = len(nodes)
for i, step in birth.items():
    last[i] = max(last.get(i, -1), step)

active, allocations, free = {}, [], []
arena, peak = 0, 0
def align(x):
    return (x + 63) // 64 * 64

def allocate(i):
    global arena, peak
    t = tensors[i]
    if physical(i) != i or t['storage'] != 'activation':
        return
    assert i not in active and i not in eliminated
    size = align(max(1, t['bytes']))
    free.sort()
    for j, (offset, length) in enumerate(free):
        if length >= size:
            free.pop(j)
            if length > size:
                free.append((offset + size, length - size))
            break
    else:
        offset = arena
        arena += size
    t['offset'] = offset
    active[i] = (offset, size)
    allocations.append(i)
    peak = max(peak, sum(size for _, size in active.values()))

for i, t in enumerate(tensors):
    if t['name'] == 'input':
        allocate(i)
for step, _, outputs, _ in events:
    # Allocate outputs before freeing this event's inputs, even at last use.
    for i in outputs:
        allocate(i)
    for i, (offset, size) in list(active.items()):
        if last[i] <= step:
            free.append((offset, size))
            del active[i]
    merged = []
    for offset, size in sorted(free):
        if merged and sum(merged[-1]) == offset:
            a, b = merged[-1]
            merged[-1] = (a, b + size)
        else:
            merged.append((offset, size))
    free[:] = merged

for i, t in enumerate(tensors):
    if i in eliminated:
        # Descriptors remain ABI-compatible; no fused dispatcher reads these.
        t['offset'] = 0
        t['eliminated_by_gelu_fusion'] = True
    elif 'view_of' in t:
        owner = tensors[t['view_of']]
        t['offset'] = owner['offset'] + t['view_byte_offset']
    t['last_use'] = last.get(physical(i), -1)

pair_checks = 0
for ix, i in enumerate(allocations):
    a = tensors[i]
    for j in allocations[ix + 1:]:
        b = tensors[j]
        live = max(birth[i], birth[j]) <= min(last[i], last[j])
        overlap = max(a['offset'], b['offset']) < min(a['offset'] + align(a['bytes']), b['offset'] + align(b['bytes']))
        assert not (live and overlap), (i, j)
        pair_checks += 1
fusion_allocations = []
for f in fusions:
    a, b = tensors[f['raw_input']], tensors[f['output']]
    assert a['storage'] == b['storage'] == 'activation'
    assert max(a['offset'], b['offset']) >= min(a['offset'] + a['bytes'], b['offset'] + b['bytes'])
    fusion_allocations.append({'start': f['start'], 'input_offset': a['offset'], 'output_offset': b['offset'], 'bytes': f['elements'], 'disjoint': True})

memory = plan['memory']
memory.update(activation_arena_bytes=arena, peak_live_activation_bytes=peak)
gelu_base = align(memory['activation_base'] + arena)
quant_base = align(gelu_base + (tables / 'gelu.bin').stat().st_size)
ram_end = align(quant_base + (tables / 'quant-thresholds.bin').stat().st_size)
assert ram_end <= memory['firmware_base'] + memory['external_axi_ram_bytes']
memory.update(gelu_table_base=gelu_base, gelu_table_bytes=(tables / 'gelu.bin').stat().st_size,
              quant_table_base=quant_base, quant_table_bytes=(tables / 'quant-thresholds.bin').stat().st_size,
              used_address_end=ram_end, external_ram_used_bytes=ram_end - memory['firmware_base'])
plan['fusion_optimization'] = {'manifest_required': True, 'fusions': len(fusions),
    'eliminated_temporaries': len(eliminated), 'execution_events': len(events),
    'fusion_allocations': fusion_allocations, 'physical_allocations': len(allocations)}
plan['scope'] = 'mandatory fused dispatcher; disjoint streaming GELU allocations, contiguous aliases, original numerical operations'
(case / 'plan.json').write_text(json.dumps(plan, indent=2) + '\n')
shutil.copyfile(source / 'constants.bin', case / 'constants.bin')
shutil.copyfile(tables / 'gelu-fusions.bin', case / 'gelu-fusions.bin')
compiled = emit(plan, case)
image = bytearray((case / 'graph.bin').read_bytes())
struct.pack_into('<I', image, 56, 1)
(case / 'graph.bin').write_bytes(image)
compiled['sha256'] = hashlib.sha256(image).hexdigest()
compiled['requires_fusion_manifest'] = True
(case / 'graph_manifest.json').write_text(json.dumps(compiled, indent=2) + '\n')
summary = {'status': 'PLAN_AND_LIFETIME_PASS_EXECUTION_PENDING', 'scope': plan['scope'],
    'physical_overlap_pairs_checked': pair_checks, 'fusion_optimization': plan['fusion_optimization'],
    'memory': memory, 'compiled': compiled}
(case / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
print(json.dumps(summary), flush=True)
