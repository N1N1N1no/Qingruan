"""Freeze a completed-node prefix comparison without claiming full inference."""
import argparse
import hashlib
import json
from pathlib import Path
import re
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--geometry', required=True, choices=['R8_C16_L4', 'R8_C32_L4', 'R16_C8_L4'])
parser.add_argument('--revision', required=True)
parser.add_argument('--image', default='ILSVRC2012_val_00000274.JPEG')
parser.add_argument('--verilator-revision', default='v1')
args = parser.parse_args()
assert all(re.fullmatch(r'[A-Za-z0-9_-]+', v) for v in [args.revision, args.verilator_revision])
assert re.fullmatch(r'ILSVRC2012_val_\d{8}\.JPEG', args.image)
vcs = ROOT / 'work/full-rv32' / args.geometry / args.image
verilator = ROOT / 'work/full-rv32-verilator' / args.geometry / args.image / args.verilator_revision
target = ROOT / 'work/inflight-cycle-crosscheck' / args.geometry / args.revision
assert not target.exists(), 'Preserve previous evidence'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def local(path):
    return BASE / path[11:] if path.startswith('/workspace/') else Path(path)


def verify(hashes):
    for name, value in hashes.items():
        assert sha(local(name)) == value, name


for directory in [vcs, verilator]:
    prep = json.loads((directory / 'preparation.json').read_text())
    verify(prep['source_sha256'])
compiled = json.loads((verilator / 'compile-summary.json').read_text())
assert sha(verilator / 'obj_dir/full_simv') == compiled['simulator_sha256']
assert sha(verilator / 'preparation.json') == compiled['preparation_sha256']
pattern = rb'FULL_MODEL_NODE_DONE node=(\d+) cycles=(\d+) CPU_cycle=(\d+)\r?\n'
logs = [(directory / 'normal.log').read_bytes() for directory in [vcs, verilator]]
matches = [list(re.finditer(pattern, data)) for data in logs]
count = min(map(len, matches))
assert count > 0
events = [[tuple(int(value) for value in match.groups()) for match in group[:count]] for group in matches]
assert events[0] == events[1], 'Actual node or CPU completion cycles differ'
assert len(set(node for node, _, _ in events[0])) == count
target.mkdir(parents=True)
prefixes = []
for index, name, directory in [(0, 'VCS', vcs), (1, 'Verilator', verilator)]:
    length = matches[index][count - 1].end()
    frozen = target / (name + '-log-prefix.txt')
    frozen.write_bytes(logs[index][:length])
    assert (directory / 'normal.log').read_bytes().startswith(frozen.read_bytes())
    prefixes.append({'simulator': name, 'original_log': str(directory / 'normal.log'),
                     'prefix_bytes': length, 'prefix_sha256': sha(frozen)})
(target / 'node-events.json').write_text(json.dumps(events[0], indent=2) + '\n')
files = [Path(__file__), vcs / 'preparation.json', verilator / 'preparation.json',
         verilator / 'compile-summary.json', target / 'VCS-log-prefix.txt', target / 'Verilator-log-prefix.txt',
         target / 'node-events.json']
result = {'status': 'COMPLETED_NODE_PREFIX_VCS_VERILATOR_CYCLES_IDENTICAL_FULL_INFERENCE_PENDING',
          'observed_at': datetime.now(timezone.utc).isoformat(), 'geometry': args.geometry, 'image': args.image,
          'completed_node_events_compared': count, 'last_node': events[0][-1][0],
          'last_common_CPU_cycle': events[0][-1][2], 'all_node_and_CPU_completion_cycles_identical': True,
          'prefixes': prefixes, 'source_sha256': {str(path): sha(path) for path in files},
          'executable_sha256': {str(path): sha(path) for path in [vcs / 'simv', verilator / 'obj_dir/full_simv']},
          'full_model_inference_accepted': False, 'end_to_end_latency_measured': False,
          'official_accuracy_accepted': False, 'final_ADP_accepted': False,
          'scope': 'Only the common prefix of completed real CPU node events. All sampled node-call and absolute CPU cycles agree exactly for identical firmware/data/geometry, and source guards are unchanged. Frozen log prefixes remain independently checkable while live logs append. This does not validate unfinished nodes, final1000 logits, full-model cycle equality, organizer accuracy or performance.'}
(target / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({key: result[key] for key in ['status', 'completed_node_events_compared', 'last_node', 'last_common_CPU_cycle']}), flush=True)
