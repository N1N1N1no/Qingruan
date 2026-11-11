"""Map the full CPU/control and streaming backend at the original SoC boundary.

The existing converted CPU/control is reused byte for byte apart from the
callee module name. Its original libraries, SDC and backend-import policy are
preserved. Negative slack remains a failed timing result.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import signal
import subprocess
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BASE = ROOT.parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--revision', default='v1')
parser.add_argument('--stage', choices=['prepare', 'map', 'all'], default='all')
args = parser.parse_args()
assert re.fullmatch(r'[A-Za-z0-9_-]+', args.revision)
case = ROOT / ('work/vecpipe-full-soc-synthesis-' + args.revision)
backend = ROOT / 'work/vecpipe-backend-synthesis-v1'
oldbackend = ROOT / 'work/backend-synthesis/R8_C16_L4/shared'
original = ROOT / 'work/full-soc-synthesis/R8_C16_L4/shared/v1'


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def local(name):
    for prefix in ['/workspace/', '/mnt/data/Ubuntu/qingruan/']:
        if name.startswith(prefix):
            return BASE / name[len(prefix):]
    return Path(name)


def verify(hashes):
    for name, expected in hashes.items():
        assert sha(local(name)) == expected, name


if args.stage in ['prepare', 'all']:
    assert not case.exists(), 'Preserve prior attempt; use a new revision'
    old = json.loads((original / 'summary.json').read_text())
    assert old['status'] in ['FULL_ALL8_SOC_MAPPED_TIMING_PASS', 'FULL_ALL8_SOC_MAPPED_TIMING_FAIL']
    assert old['SRAMs'] == 128 and old['clock_period_ns'] == 10
    verify(old['inputs_sha256'])
    for name, expected in old['artifact_sha256'].items():
        assert sha(original / name) == expected, name
    case.mkdir()
    control = (original / 'soc_control_dc.v').read_text()
    needle = '  axi_accel_backend u_backend ('
    assert control.count(needle) == 1
    (case / 'soc_control_dc.v').write_text(control.replace(needle, '  axi_accel_backend_vecpipe u_backend ('))
    script = (original / 'run.tcl').read_text()
    for prefix in ['/workspace/', '/mnt/data/Ubuntu/qingruan/']:
        script = script.replace(prefix, str(BASE) + '/')
    script = script.replace(str(original), str(case)).replace(str(oldbackend), str(backend))
    script = script.replace('axi_accel_backend', 'axi_accel_backend_vecpipe')
    (case / 'run.tcl').write_text(script)
    paths = [Path(__file__), ROOT / 'synthesize_full_soc.py', original / 'summary.json',
             original / 'run.tcl', original / 'soc_control_dc.v',
             *[local(name) for name in old['inputs_sha256']],
             case / 'soc_control_dc.v', case / 'run.tcl']
    hashes = {str(path): sha(path) for path in dict.fromkeys(paths)}
    prep = {'status': 'VECPIPE_FULL_SOC_CONTROL_PREPARED_MAPPING_PENDING',
            'geometry': 'R8_C16_L4', 'source_sha256': hashes,
            'original_control_only_callee_name_changed': True,
            'original_libraries_SDC_boundary_preserved': True, 'SRAMs': 128,
            'clock_period_ns': 10,
            'scope': 'Complete CPU/router/MMIO/arbiter/all8 backend/one array; original external RAM boundary preserved. No timing or area acceptance from preparation.'}
    (case / 'preparation.json').write_text(json.dumps(prep, indent=2) + '\n')
    if args.stage == 'prepare':
        print(prep['status'], flush=True)
        raise SystemExit(0)
else:
    prep = json.loads((case / 'preparation.json').read_text())

assert not (case / 'synthesis.log').exists() and not (case / 'summary.json').exists(), 'Preserve started attempt'
verify(prep['source_sha256'])
proof = json.loads((backend / 'summary.json').read_text())
assert proof['status'] in ['VECPIPE_BACKEND_MAPPED_SETUP_PASS', 'VECPIPE_BACKEND_MAPPED_SETUP_FAIL']
assert proof['SRAMs'] == 128 and proof['clock_period_ns'] == 10
verify(proof['source_sha256'])
for name, expected in proof['artifact_sha256'].items():
    assert sha(backend / name) == expected, name
hashes = dict(prep['source_sha256'])
hashes.update({str(backend / name): sha(backend / name) for name in ['summary.json', 'mapped.ddc', 'constraints.sdc']})
hashes[str(case / 'preparation.json')] = sha(case / 'preparation.json')


def limits():
    resource.setrlimit(resource.RLIMIT_AS, (12 * 1024**3, 12 * 1024**3))


print('VECPIPE_FULL_SOC_SYNTHESIS_PHASE map', flush=True)
started = time.monotonic()
with (case / 'synthesis.log').open('w') as log:
    process = subprocess.Popen(['dc_shell', '-f', str(case / 'run.tcl')], cwd=case,
                               stdout=log, stderr=subprocess.STDOUT,
                               start_new_session=True, preexec_fn=limits)
    try:
        while True:
            try:
                code = process.wait(timeout=30)
                break
            except subprocess.TimeoutExpired:
                print('VECPIPE_FULL_SOC_SYNTHESIS_RUNNING elapsed_s=%d' % (time.monotonic() - started), flush=True)
                if time.monotonic() - started > 7200:
                    raise TimeoutError('full SoC mapping')
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=10)
log = (case / 'synthesis.log').read_text(errors='replace')
assert code == 0 and 'FULL_ALL8_SOC_SYNTHESIS_MAPPED' in log and 'Thank you...' in log
assert not re.search(r'^Error:', log, re.M)
verify(hashes)


def slack(name):
    return float(re.search(r'slack\s+\([^)]+\)\s+(-?[\d.]+)', (case / name).read_text())[1])


setup, hold = slack('setup.rpt'), slack('hold.rpt')
artifacts = ['area.rpt', 'setup.rpt', 'hold.rpt', 'violations.rpt', 'timing_check.rpt',
             'check_design.rpt', 'hierarchy.rpt', 'units.rpt', 'mapped.ddc', 'mapped.v',
             'constraints.sdc', 'mapped.sdf', 'run.tcl', 'synthesis.log']
assert all((case / name).stat().st_size > 0 for name in artifacts)
summary = {**prep, 'status': 'VECPIPE_FULL_ALL8_SOC_MAPPED_TIMING_PASS' if min(setup, hold) >= 0 else 'VECPIPE_FULL_ALL8_SOC_MAPPED_TIMING_FAIL',
           'source_sha256': hashes, 'area': float(re.search(r'Total cell area:\s+([\d.]+)', (case / 'area.rpt').read_text())[1]),
           'setup_slack_ns': setup, 'hold_slack_ns': hold, 'elapsed_s': time.monotonic() - started,
           'artifact_sha256': {name: sha(case / name) for name in artifacts},
           'official_accuracy_accepted': False, 'full_inference_accepted': False,
           'final_ADP_accepted': False, 'gate_SDF_functional_simulation_passed': False,
           'scope': 'Actual complete SoC mapping; exact original CPU/control except backend callee name, same libraries/SDC/IO boundary/array count. Negative slack is not accepted; whole-image correctness, timing closure and gate/SDF proofs still required.'}
(case / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
print(json.dumps({key: summary[key] for key in ['status', 'area', 'setup_slack_ns', 'hold_slack_ns', 'SRAMs']}), flush=True)
