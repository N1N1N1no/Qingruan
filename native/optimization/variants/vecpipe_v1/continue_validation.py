"""Finite dependency queue for the already-authorized local candidate checks.

Launches full RV32 only after actual RV32+negative qualification; launches
full-SoC mapping only after backend mapping has produced verified artifacts.
It neither restarts nor stops any of the pre-existing verification processes.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
case = ROOT / 'work/vecpipe-continuation-v1'
assert not case.exists(), 'Preserve prior queue; do not launch duplicate jobs'
case.mkdir()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


scripts = [HERE / name for name in ['run_full_model.py', 'synthesize_soc.py', 'run_soc.py',
                                   'synthesize_backend.py', 'continue_validation.py']]
hashes = {str(path): sha(path) for path in scripts}
tasks = [
    {'name': 'full_RV32', 'prerequisite': ROOT / 'work/vecpipe-soc-v1/summary.json',
     'prerequisite_status': ['VECPIPE_REAL_RV32_ALL8_SHARED_BACKEND_FROZEN_FIRMWARE_AND_CPU_NEGATIVE_PASS'],
     'prerequisite_log': ROOT / 'vecpipe-soc-v1.log',
     'argv': [sys.executable, '-B', HERE / 'run_full_model.py', '--stage', 'run'],
     'result': ROOT / 'work/vecpipe-full-rv32-v1/summary.json'},
    {'name': 'full_SoC_mapping', 'prerequisite': ROOT / 'work/vecpipe-backend-synthesis-v1/summary.json',
     'prerequisite_status': ['VECPIPE_BACKEND_MAPPED_SETUP_PASS', 'VECPIPE_BACKEND_MAPPED_SETUP_FAIL'],
     'prerequisite_log': ROOT / 'vecpipe-backend-synthesis-v1.log',
     'argv': [sys.executable, '-B', HERE / 'synthesize_soc.py', '--stage', 'map'],
     'result': ROOT / 'work/vecpipe-full-soc-synthesis-v1/summary.json'}]
state = {task['name']: {'status': 'WAITING_FOR_QUALIFICATION'} for task in tasks}
processes = {}
streams = {}
started = time.monotonic()


def save():
    (case / 'progress.json').write_text(json.dumps({'status': 'FINITE_LOCAL_VALIDATION_QUEUE_ACTIVE',
        'source_sha256': hashes, 'tasks': state, 'elapsed_s': time.monotonic() - started,
        'scope': 'Execution state only. No functional, accuracy, timing or final performance acceptance.'}, indent=2) + '\n')


try:
    while any(item['status'] not in ['TERMINAL', 'PREREQUISITE_FAILED'] for item in state.values()):
        for task in tasks:
            name = task['name']
            if state[name]['status'] == 'WAITING_FOR_QUALIFICATION':
                if task['prerequisite'].exists():
                    proof = json.loads(task['prerequisite'].read_text())
                    assert proof['status'] in task['prerequisite_status']
                    for path, expected in hashes.items():
                        assert sha(Path(path)) == expected, path
                    assert not task['result'].exists(), 'Do not overwrite completed validation'
                    argv = list(map(str, task['argv']))
                    stream = (case / (name + '.log')).open('w')
                    process = subprocess.Popen(argv, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
                    processes[name] = process
                    streams[name] = stream
                    state[name] = {'status': 'RUNNING', 'pid': process.pid, 'argv': argv,
                                   'prerequisite': str(task['prerequisite']),
                                   'prerequisite_sha256': sha(task['prerequisite'])}
                    print('VECPIPE_QUEUE_LAUNCHED ' + name + ' pid=' + str(process.pid), flush=True)
                elif (task['prerequisite_log'].exists() and
                      'Traceback (most recent call last)' in task['prerequisite_log'].read_text(errors='replace')):
                    state[name] = {'status': 'PREREQUISITE_FAILED', 'log': str(task['prerequisite_log'])}
                    print('VECPIPE_QUEUE_PREREQUISITE_FAILED ' + name, flush=True)
                elif time.monotonic() - started > 10800:
                    state[name] = {'status': 'PREREQUISITE_FAILED', 'reason': 'Three-hour dependency deadline; existing process not stopped'}
            elif state[name]['status'] == 'RUNNING':
                code = processes[name].poll()
                if code is not None:
                    streams[name].close()
                    state[name].update({'status': 'TERMINAL', 'returncode': code,
                                        'result_exists': task['result'].exists()})
                    if task['result'].exists():
                        result = json.loads(task['result'].read_text())
                        state[name].update({'result_status': result['status'], 'result': str(task['result']),
                                            'result_sha256': sha(task['result'])})
                    print('VECPIPE_QUEUE_TERMINAL ' + name + ' returncode=' + str(code), flush=True)
        save()
        if any(item['status'] in ['WAITING_FOR_QUALIFICATION', 'RUNNING'] for item in state.values()):
            time.sleep(15)
finally:
    # Never terminate somebody else's process or an in-progress authorized run.
    for stream in streams.values():
        stream.close()
save()
ok = all(item['status'] == 'TERMINAL' and item.get('returncode') == 0 and item.get('result_exists') for item in state.values())
(case / 'summary.json').write_text(json.dumps({'status': 'FINITE_LOCAL_QUEUE_FINISHED' if ok else 'FINITE_LOCAL_QUEUE_DIAGNOSTIC_FAILURE',
    'tasks': state, 'source_sha256': hashes, 'elapsed_s': time.monotonic() - started,
    'scope': 'Queue completion only; inspect individual hardware proof statuses. No organizer accuracy or final ADP acceptance.'}, indent=2) + '\n')
assert ok, str(case / 'summary.json')
print('VECPIPE_QUEUE_FINISHED_REVIEW_INDIVIDUAL_HARDWARE_PROOFS', flush=True)
