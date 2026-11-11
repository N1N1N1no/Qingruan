"""Apply the qualified whole-SoC closure procedure to an isolated variant.

Uses the unchanged original 10ns/uncertainty/IO SDC. Timing optimization and
noninverting buffers are local mapped-cell changes; RTL and references stay
frozen. Setup/hold failure is recorded as failure.
"""
import argparse
import ast
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
PROJECT = BASE / 'native/audits/20261107/riscv_pe_contest/contest/task1/my_design/project'
parser = argparse.ArgumentParser()
parser.add_argument('--source-revision', default='v1')
parser.add_argument('--revision', default='v1')
args = parser.parse_args()
for value in [args.source_revision, args.revision]:
    assert re.fullmatch(r'[A-Za-z0-9_-]+', value)
source = ROOT / ('work/vecpipe-full-soc-synthesis-' + args.source_revision)
case = ROOT / ('work/vecpipe-full-soc-timing-' + args.revision)
assert not case.exists(), 'Preserve prior closure attempt'


def local(name):
    for prefix in ['/workspace/', '/mnt/data/Ubuntu/qingruan/']:
        if name.startswith(prefix):
            return BASE / name[len(prefix):]
    return Path(name)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def verify(hashes):
    for name, expected in hashes.items():
        assert sha(local(name)) == expected, name


proof = json.loads((source / 'summary.json').read_text())
assert proof['status'] in ['VECPIPE_FULL_ALL8_SOC_MAPPED_TIMING_PASS', 'VECPIPE_FULL_ALL8_SOC_MAPPED_TIMING_FAIL']
assert proof['geometry'] == 'R8_C16_L4' and proof['SRAMs'] == 128 and proof['clock_period_ns'] == 10
verify(proof['source_sha256'])
for name, expected in proof['artifact_sha256'].items():
    assert sha(source / name) == expected, name
backend = ROOT / 'work/vecpipe-backend-synthesis-v1'
backend_proof = json.loads((backend / 'summary.json').read_text())
verify(backend_proof['source_sha256'])
for name, expected in backend_proof['artifact_sha256'].items():
    assert sha(backend / name) == expected, name

# Extract only the original literal TCL, without importing/executing its driver.
template_path = ROOT / 'close_full_soc_timing.py'
tree = ast.parse(template_path.read_text())
templates = [node.value.value for node in tree.body if isinstance(node, ast.Assign)
             and any(isinstance(target, ast.Name) and target.id == 'tcl' for target in node.targets)
             and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
             and 'FULL_SOC_TIMING_CLOSURE_MAPPED' in node.value.value]
assert len(templates) == 1
tcl = templates[0]
case.mkdir()
std, sram = [PROJECT / 'build/dc_libraries' / name for name in ['standard.db', 'sram.db']]
original_sdc = PROJECT / 'synth/soc_io.sdc'
values = dict(STD=std, SRAM=sram, SOURCE=source / 'mapped.ddc', ORIGINAL_SDC=original_sdc,
              INTERIM_SETUP=case / 'incremental_setup.rpt', INTERIM_HOLD=case / 'incremental_hold.rpt',
              INTERIM_DDC=case / 'incremental.ddc', EDITS=case / 'hold-edits.tsv',
              AREA=case / 'area.rpt', SETUP=case / 'setup.rpt', HOLD=case / 'hold.rpt',
              VIOLATIONS=case / 'violations.rpt', TIMING_CHECK=case / 'timing_check.rpt',
              DESIGN_CHECK=case / 'check_design.rpt', UNITS=case / 'units.rpt',
              HIERARCHY=case / 'hierarchy.rpt', DDC=case / 'mapped.ddc',
              VERILOG=case / 'mapped.v', SDC=case / 'constraints.sdc', SDF=case / 'mapped.sdf')
for key, value in values.items():
    assert '{' + key + '}' in tcl, key
    tcl = tcl.replace('{' + key + '}', '{' + str(value) + '}')
tcl = tcl.replace('SRAM_COUNT', '128')
tcl = 'if {[catch {\n' + tcl + '\n} closure_error]} {puts stderr "VECPIPE_FULL_SOC_TIMING_CLOSURE_FAILED: $closure_error"; exit 1}\nexit 0\n'
(case / 'run.tcl').write_text(tcl)
paths = [Path(__file__), template_path, source / 'summary.json', source / 'mapped.ddc',
         source / 'mapped.v', source / 'constraints.sdc', backend / 'summary.json',
         std, sram, original_sdc, case / 'run.tcl']
hashes = {str(path): sha(path) for path in paths}
preparation = {'status': 'VECPIPE_FULL_SOC_CLOSURE_PREPARED_EXECUTION_PENDING',
               'geometry': 'R8_C16_L4', 'source_case': str(source), 'source_sha256': hashes,
               'original_source_sha256': proof['source_sha256'],
               'original_setup_slack_ns': proof['setup_slack_ns'],
               'original_hold_slack_ns': proof['hold_slack_ns'],
               'clock_period_ns': 10, 'clock_or_constraints_relaxed': False,
               'original_TCL_template_reused_without_algorithm_changes': True,
               'scope': 'Unchanged original complete-SoC setup/hold closure procedure on a new DDC copy. No RTL/reference edits or timing relaxation.'}
(case / 'preparation.json').write_text(json.dumps(preparation, indent=2) + '\n')


def limits():
    resource.setrlimit(resource.RLIMIT_AS, (12 * 1024**3, 12 * 1024**3))


argv = ['dc_shell', '-f', str(case / 'run.tcl')]
started = time.monotonic()
print('VECPIPE_FULL_SOC_TIMING_CLOSURE_PHASE map', flush=True)
with (case / 'run.log').open('w') as log:
    process = subprocess.Popen(argv, cwd=case, stdout=log, stderr=subprocess.STDOUT,
                               start_new_session=True, preexec_fn=limits)
    (case / 'live-process.json').write_text(json.dumps({'status': 'RUNNING', 'pid': process.pid, 'argv': argv}, indent=2) + '\n')
    try:
        while True:
            try:
                code = process.wait(timeout=30)
                break
            except subprocess.TimeoutExpired:
                print('VECPIPE_FULL_SOC_TIMING_CLOSURE_RUNNING elapsed_s=%d' % (time.monotonic() - started), flush=True)
                if time.monotonic() - started > 7200:
                    raise TimeoutError('whole-SoC timing closure')
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
        (case / 'live-process.json').write_text(json.dumps({'status': 'TERMINAL', 'returncode': process.returncode}, indent=2) + '\n')
log = (case / 'run.log').read_text(errors='replace')
verify(hashes)
verify(proof['source_sha256'])
if code != 0 or 'FULL_SOC_TIMING_CLOSURE_MAPPED' not in log or re.search(r'^Error:', log, re.M):
    (case / 'failure.json').write_text(json.dumps({**preparation, 'status': 'VECPIPE_FULL_SOC_CLOSURE_TOOL_FAILURE',
        'returncode': code, 'elapsed_s': time.monotonic() - started}, indent=2) + '\n')
    raise RuntimeError(str(case / 'run.log'))
assert 'Thank you...' in log
artifacts = ['area.rpt', 'setup.rpt', 'hold.rpt', 'violations.rpt', 'timing_check.rpt',
             'check_design.rpt', 'hierarchy.rpt', 'units.rpt', 'mapped.ddc', 'mapped.v',
             'constraints.sdc', 'mapped.sdf', 'hold-edits.tsv', 'run.tcl', 'run.log',
             'incremental_setup.rpt', 'incremental_hold.rpt', 'incremental.ddc']
assert all((case / name).stat().st_size > 0 for name in artifacts)


def slack(name):
    return float(re.search(r'slack\s+\([^)]+\)\s+(-?[\d.]+)', (case / name).read_text())[1])


setup, hold = slack('setup.rpt'), slack('hold.rpt')
result = {**preparation, 'status': 'VECPIPE_FULL_ALL8_SOC_CLOSED_TIMING_PASS' if min(setup, hold) >= 0 else 'VECPIPE_FULL_ALL8_SOC_CLOSED_TIMING_FAIL',
          'area': float(re.search(r'Total cell area:\s+([\d.]+)', (case / 'area.rpt').read_text())[1]),
          'setup_slack_ns': setup, 'hold_slack_ns': hold, 'SRAMs': 128,
          'inserted_D_path_buffers': len((case / 'hold-edits.tsv').read_text().splitlines()) - 1,
          'elapsed_s': time.monotonic() - started, 'artifact_sha256': {name: sha(case / name) for name in artifacts},
          'gate_SDF_functional_simulation_passed': False, 'official_accuracy_accepted': False,
          'full_inference_accepted': False, 'final_ADP_accepted': False,
          'scope': 'Complete SoC after unchanged-constraint timing optimization and genuine D-path hold buffers. Negative timing remains a failure. SDF numeric path audit and actual gate execution remain necessary.'}
(case / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({key: result[key] for key in ['status', 'area', 'setup_slack_ns', 'hold_slack_ns', 'inserted_D_path_buffers']}), flush=True)
