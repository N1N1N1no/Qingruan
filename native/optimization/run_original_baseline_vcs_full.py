"""Original full CPU+PE VCS benchmark with archived inputs and frozen runtime.

Historical baseline sources are copied after matching the qualified compiled
inputs, so subsequent source development cannot invalidate this long run.
All actual simulator binaries/shared objects remain guarded before/after.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import signal
import struct
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parents[1]
PROJECT = BASE / 'native/audits/20261107/riscv_pe_contest/contest/task1/my_design/project'
sys.path.insert(0, str(PROJECT / 'scripts'))
from run_pe_axi_sdf import compiled_runtime_files

template = ROOT / 'work/original-baseline-rtl/vcs/v2'
qualification = ROOT / 'work/original-baseline-vcs-qualification-v2/summary.json'
case = ROOT / 'work/original-baseline-full-vcs/v1'
assert not case.exists(), 'Preserve prior attempt'
wall_limit_s = 2592000
image = 'ILSVRC2012_val_00000274.JPEG'
firmware = ROOT / 'work/original-baseline-full' / image


def local(path):
    if path.startswith('/workspace/'):
        return BASE / path[11:]
    if path.startswith('/mnt/data/Ubuntu/qingruan/'):
        return BASE / path[len('/mnt/data/Ubuntu/qingruan/'):]
    return Path(path)


def sha(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def verify(hashes):
    for path, value in hashes.items():
        assert sha(local(path)) == value, path


def save(name, data):
    (case / name).write_text(json.dumps(data, indent=2) + '\n')


prep = json.loads((template / 'preparation.json').read_text())
compiled = json.loads((template / 'compile-summary.json').read_text())
qualified = json.loads(qualification.read_text())
assert qualified['status'] == 'ORIGINAL_VCS_SOURCE_IDENTITY_AND_1M_CPU_CYCLE_PREFIX_QUALIFIED_FULL_INFERENCE_PENDING'
assert qualified['ordered_original_source_header_and_directive_entries_identical'] == 68
assert qualified['unmodified_original_arithmetic_RTL_used_by_VCS']
verify(qualified['source_sha256'])
verify(prep['source_sha256'])
assert sha(template / 'preparation.json') == compiled['preparation_sha256']
executable = template / 'simv'
assert sha(executable) == compiled['simulator_sha256']
runtime = compiled_runtime_files(template)
assert runtime

# Require both longer bounded real-CPU samples to have finished before choosing
# the long-run setup; their outputs must have identical complete node events.
samples = [ROOT / 'work/original-baseline-rtl/verilator/benchmark-v1', template]
events, reports = [], []
for directory in samples:
    measured = json.loads((directory / 'prefix-20000000-summary.json').read_text())
    assert measured['status'] == 'ORIGINAL_CPU_PE_PREFIX_ONLY_NOT_INFERENCE_OR_PERFORMANCE_ACCEPTANCE'
    assert not measured['full_model_accepted']
    events.append(re.findall(r'ORIGINAL_BASELINE_NODE_DONE node=(\d+) cycles=(\d+) CPU_cycle=(\d+)',
                           (directory / 'prefix-20000000.log').read_text()))
    reports.append((directory / 'prefix-20000000-report.txt').read_bytes())
assert events[0] == events[1] and len(events[0]) == 385 and reports[0] == reports[1]

case.mkdir(parents=True)
sources = dict(prep['source_sha256'])
extra = [Path(__file__), PROJECT / 'scripts/run_pe_axi_sdf.py', qualification,
         template / 'preparation.json', template / 'compile-summary.json', firmware / 'preparation.json']
extra += [directory / name for directory in samples for name in
          ['prefix-20000000-summary.json', 'prefix-20000000.log', 'prefix-20000000-report.txt']]
sources.update({str(path): sha(path) for path in extra})
snapshots = {}
for name, expected in sources.items():
    source = local(name)
    assert sha(source) == expected, name
    destination = case / 'input-snapshot' / source.relative_to(BASE)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        destination.write_bytes(source.read_bytes())
    assert sha(destination) == expected
    snapshots[name] = {'archive': str(destination.relative_to(case)), 'sha256': expected}
snapshot_hashes = {str(case / item['archive']): item['sha256'] for item in snapshots.values()}
verify(snapshot_hashes)
assert compiled_runtime_files(template) == runtime

data_hex = case / 'input-snapshot' / (firmware / 'data.hex').relative_to(BASE)
expected_logits = case / 'input-snapshot' / (ROOT / 'work/fusion-proof' / (image + '.baseline')).relative_to(BASE)
assert data_hex.exists() and expected_logits.stat().st_size == 4000
argv = [str(executable), '+MEM_HEX=' + str(data_hex), '+MAX_CYCLES=1000000000000',
        '+REPORT=' + str(case / 'report.txt'), '+LOGITS=' + str(case / 'logits.bin'),
        *('+' + key + '=' + value for key, value in prep['parameters'].items())]
save('preparation.json', {'status': 'ORIGINAL_FULL_VCS_ARCHIVED_INPUTS_RUNTIME_FROZEN_EXECUTION_PENDING',
     'image': image, 'parameters': prep['parameters'], 'template': str(template),
     'compiled_executable': str(executable), 'simulator_sha256': compiled['simulator_sha256'],
     'runtime_files': runtime, 'source_sha256': snapshot_hashes, 'original_source_archive_map': snapshots,
     'wall_limit_s': wall_limit_s, 'argv': argv, 'clock_period_ns': 10, 'RAM_response_delay_ns': 1,
     'actual_original_RTL_used': True, 'source_updates_outside_snapshot_permitted': True,
     'full_model_accepted': False, 'official_accuracy_accepted': False,
     'scope': 'Same original unfused1396-node/77MAC graph, original C/MMIO signed-magnitude four-plane PE, original raw RTL and same1000 frozen-C reference. Every compile/source/firmware input is first verified then independently copied. Runtime loads the byte-identical archived RAM image; actual compiled binaries/shared objects remain guarded. Complete real CPU inference still required.'})
print('ORIGINAL_FULL_VCS_ARCHIVE_READY image=' + image, flush=True)


def limits():
    resource.setrlimit(resource.RLIMIT_AS, (8 * 1024**3, 8 * 1024**3))


start = time.monotonic()
with (case / 'normal.log').open('w') as stream:
    child = subprocess.Popen(argv, cwd=case, stdout=stream, stderr=subprocess.STDOUT,
                             start_new_session=True, preexec_fn=limits)
    save('live-process.json', {'status': 'RUNNING', 'pid': child.pid, 'argv': argv,
                              'wall_limit_s': wall_limit_s, 'started_unix': time.time()})
    try:
        while True:
            try:
                code = child.wait(timeout=30)
                break
            except subprocess.TimeoutExpired:
                print('ORIGINAL_FULL_VCS_RUNNING elapsed_s=%d' % (time.monotonic() - start), flush=True)
                if time.monotonic() - start > wall_limit_s:
                    raise TimeoutError('Original full VCS wall limit')
    finally:
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=10)
        save('live-process.json', {'status': 'TERMINAL', 'returncode': child.returncode})
elapsed = time.monotonic() - start
verify(snapshot_hashes)
assert compiled_runtime_files(template) == runtime, 'Actual loaded runtime files changed'
assert sha(executable) == compiled['simulator_sha256']
log = (case / 'normal.log').read_text(errors='replace')
passed = code == 0 and 'ORIGINAL_BASELINE_FULL_MODEL_PASS nodes=1396 MAC_jobs=77' in log and 'ORIGINAL_BASELINE_CPU_RETURN_PASS' in log
passed = passed and not re.search(r'(?i)Fatal:|Error-\[|ORIGINAL_BASELINE_FAIL|AXI_RX_(?:SETUP_FAIL|UNKNOWN)', log)
result = {'status': 'ORIGINAL_CPU_PE_FULL_RV32_SINGLE_IMAGE_FROZEN_C_PASS_OFFICIAL_ACCURACY_OPEN' if passed else 'ORIGINAL_FULL_VCS_DIAGNOSTIC_FAILURE',
          'image': image, 'full_model_accepted': passed, 'simulator_kind': 'VCS2016_original_raw_RTL',
          'original_RTL_unmodified': True, 'source_archive_verified': True, 'runtime_files_unchanged': True,
          'source_sha256': snapshot_hashes, 'simulator_sha256': compiled['simulator_sha256'],
          'compiled_executable': str(executable), 'runtime_files': runtime, 'template': str(template),
          'commands': [{'phase': 'full', 'argv': argv, 'returncode': code, 'elapsed_s': elapsed,
                        'wall_limit_s': wall_limit_s}],
          'official_accuracy_accepted': False, 'before_after_ADP_accepted': False,
          'scope': 'Complete original CPU+raw PE RTL with unchanged original C, archived same input/weights/frozen reference. Monitor checks all1396 nodes/77MACjobs, actual MMIO counts and all1000 frozen-C logits through the existing internal criterion. Historical source updates are independent of immutable archives; actual simulator runtime remains checked. Not organizer accuracy or final ADP.'}
if (case / 'report.txt').exists():
    result['report'] = (case / 'report.txt').read_text()
if (case / 'logits.bin').exists():
    data = (case / 'logits.bin').read_bytes()
    assert len(data) == 4000 and all(abs(value) < float('inf') for value in struct.unpack('<1000f', data))
    result.update(logits_sha256=sha(case / 'logits.bin'), logits_byte_exact=data == expected_logits.read_bytes())
save('summary.json' if passed else 'failure.json', result)
assert passed, str(case / 'failure.json')
print(result['status'], flush=True)
