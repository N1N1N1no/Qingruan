"""Whole-graph proof of the streamable allocation, including mandatory dispatch."""
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parents[1]
SWIN = BASE / 'native/swin-tiny'
plan = ROOT / 'work/fused-plan'
baseline = ROOT / 'work/fusion-proof'
tables = ROOT / 'work/lookup-tables'
case = ROOT / 'work/fused-plan-proof'
case.mkdir(parents=True, exist_ok=True)
subprocess.run(['/usr/bin/gcc', '-O3', '-march=native', '-ffp-contract=off', '-DSWIN_HOST',
    '-I' + str(SWIN / 'c'), str(SWIN / 'c/graph.c'), str(SWIN / 'c/pe_driver.c'),
    str(ROOT / 'c/host_opt.c'), '-lm', '-o', str(case / 'host_opt')], check=True)
items = []
for image in sorted((BASE / 'competition/images').glob('*.JPEG')):
    output = case / (image.name + '.logits')
    argv = [case / 'host_opt', plan / 'graph.bin', plan / 'constants.bin',
            baseline / (image.name + '.input'), output, tables / 'gelu.bin', plan / 'gelu-fusions.bin']
    print('FUSED_PLAN_PROOF ' + image.name, flush=True)
    with (case / (image.name + '.log')).open('w') as log:
        subprocess.run(list(map(str, argv)), stdout=log, stderr=subprocess.STDOUT, timeout=120, check=True)
    expected = baseline / (image.name + '.baseline')
    assert output.read_bytes() == expected.read_bytes(), image.name
    items.append({'image': image.name, 'bit_exact_logits': True, 'sha256': hashlib.sha256(output.read_bytes()).hexdigest()})
assert len(items) == 20
with (case / 'negative.log').open('w') as log:
    rejected = subprocess.run(list(map(str, [case / 'host_opt', plan / 'graph.bin', plan / 'constants.bin',
        baseline / (items[0]['image'] + '.input'), case / 'must-not-exist.logits'])), stdout=log, stderr=subprocess.STDOUT)
assert rejected.returncode == 9 and 'fusion manifest required' in (case / 'negative.log').read_text()
summary = {'status': 'PASS', 'images': 20, 'results': items, 'missing_manifest_rejected': True,
    'plan': json.loads((plan / 'summary.json').read_text()),
    'scope': 'all20 full C logits byte-identical to frozen C baseline with disjoint streaming allocations; hardware and official accuracy remain separate'}
(case / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
print('FUSED_PLAN_ALL20_BIT_EXACT_PASS', flush=True)
