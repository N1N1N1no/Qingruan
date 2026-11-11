"""Finite, serial continuation from full-SoC mapping to required STA closure."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
case = ROOT / 'work/vecpipe-timing-continuation-v1'
assert not case.exists(), 'Do not launch duplicate closure queue'
case.mkdir()
source = ROOT / 'work/vecpipe-full-soc-synthesis-v1/summary.json'
upstream = ROOT / 'work/vecpipe-continuation-v1/progress.json'
driver = HERE / 'close_soc_timing.py'
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
hashes = {str(path): sha(path) for path in [Path(__file__), driver, ROOT / 'close_full_soc_timing.py']}
started = time.monotonic()
state = {'status': 'WAITING_FOR_FULL_SOC_MAPPING', 'source_sha256': hashes,
         'scope': 'Finite local dependency queue only; no timing, gate or final performance acceptance.'}


def save():
    state['elapsed_s'] = time.monotonic() - started
    (case / 'progress.json').write_text(json.dumps(state, indent=2) + '\n')


while not source.exists():
    save()
    if upstream.exists():
        try:
            item = json.loads(upstream.read_text())['tasks']['full_SoC_mapping']
        except json.JSONDecodeError:
            item = {}
        assert item.get('status') != 'PREREQUISITE_FAILED', 'Upstream backend failed; do not launch closure'
        assert not (item.get('status') == 'TERMINAL' and item.get('returncode') != 0), 'Upstream full-SoC mapping failed'
    assert time.monotonic() - started < 10800, 'Three-hour dependency deadline; existing job is not stopped'
    time.sleep(15)
# Source proof may have just been opened by the producer; retry a partial write.
for attempt in range(10):
    try:
        proof = json.loads(source.read_text())
        break
    except json.JSONDecodeError:
        assert attempt < 9
        time.sleep(1)
assert proof['status'] in ['VECPIPE_FULL_ALL8_SOC_MAPPED_TIMING_PASS', 'VECPIPE_FULL_ALL8_SOC_MAPPED_TIMING_FAIL']
for path, expected in hashes.items():
    assert sha(Path(path)) == expected, path
state.update({'source_proof': str(source), 'source_proof_sha256': sha(source),
              'raw_setup_slack_ns': proof['setup_slack_ns'], 'raw_hold_slack_ns': proof['hold_slack_ns']})
if min(proof['setup_slack_ns'], proof['hold_slack_ns']) >= 0:
    state['status'] = 'RAW_STA_PASS_NO_ADDITIONAL_CLOSURE_RUN'
    state['scope'] = 'Raw setup/hold already pass; SDF numeric path and actual gate checks remain required.'
else:
    argv = [sys.executable, '-B', str(driver)]
    with (case / 'closure.log').open('w') as log:
        child = subprocess.Popen(argv, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        state.update({'status': 'RUNNING_REQUIRED_CLOSURE', 'pid': child.pid, 'argv': argv})
        save()
        print('VECPIPE_TIMING_QUEUE_LAUNCHED_CLOSURE pid=' + str(child.pid), flush=True)
        while child.poll() is None:
            time.sleep(15)
            save()
    state.update({'status': 'CLOSURE_EXECUTION_TERMINAL', 'returncode': child.returncode})
    result = ROOT / 'work/vecpipe-full-soc-timing-v1/summary.json'
    if result.exists():
        data = json.loads(result.read_text())
        state.update({'result': str(result), 'result_status': data['status'], 'result_sha256': sha(result)})
    save()
    assert child.returncode == 0 and result.exists(), 'Inspect actual closure failure; no acceptance'
save()
(case / 'summary.json').write_text(json.dumps(state, indent=2) + '\n')
print('VECPIPE_TIMING_QUEUE_FINISHED_REVIEW_ACTUAL_STA_STATUS', flush=True)
