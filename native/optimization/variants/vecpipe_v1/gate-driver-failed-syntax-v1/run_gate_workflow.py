"""Finite full gate workflow, with source/count audits before any acceptance.

Prepare/compile, prove every diagnostic, exercise an actual timing violation,
then run normal and CPU-corruption stages on the same frozen simulator. All
pre-existing RTL/gate/full-model jobs remain untouched.
"""
import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BASE = ROOT.parents[1]
case = ROOT / 'work/vecpipe-gate-workflow-v1'
assert not case.exists(), 'Do not launch a duplicate gate workflow'
case.mkdir()
generation = HERE / 'gate-drivers-generation.json'
data = json.loads(generation.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def local(name):
    for prefix in ['/workspace/', '/mnt/data/Ubuntu/qingruan/']:
        if name.startswith(prefix):
            return BASE / name[len(prefix):]
    return Path(name)


hashes = {str(generation): sha(generation), **data['source_sha256']}


def verify():
    for name, expected in hashes.items():
        assert sha(local(name)) == expected, name


state = {'status': 'GATE_WORKFLOW_PREPARING', 'source_sha256': hashes, 'phases': [],
         'scope': 'Workflow state only. Gate, full-model accuracy and final ADP are separate acceptance gates.'}


def save():
    (case / 'progress.json').write_text(json.dumps(state, indent=2) + '\n')


def limits():
    resource.setrlimit(resource.RLIMIT_AS, (12 * 1024**3, 12 * 1024**3))


def run(script, extra, label, timeout=7200):
    verify()
    argv = [sys.executable, '-B', str(HERE / script), *extra]
    print('VECPIPE_GATE_WORKFLOW_PHASE ' + label, flush=True)
    started = time.monotonic()
    with (case / (label + '.log')).open('w') as log:
        process = subprocess.Popen(argv, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True, preexec_fn=limits)
        state['active_phase'] = {'label': label, 'pid': process.pid, 'argv': argv}
        save()
        try:
            while True:
                try:
                    code = process.wait(timeout=30)
                    break
                except subprocess.TimeoutExpired:
                    print('VECPIPE_GATE_WORKFLOW_RUNNING %s elapsed_s=%d' % (label, time.monotonic() - started), flush=True)
                    if time.monotonic() - started > timeout:
                        raise TimeoutError(label)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=10)
    state['phases'].append({'label': label, 'argv': argv, 'returncode': code, 'elapsed_s': time.monotonic() - started})
    save()
    assert code == 0, str(case / (label + '.log'))
    verify()


run('prepare_gate.py', ['--revision', 'v1'], 'prepare')
run('run_gate.py', ['--stage', 'compile', '--revision', 'v1'], 'compile')
run('audit_gate_connections.py', ['--kind', 'wires', '--revision', 'v1'], 'wires')
run('audit_gate_connections.py', ['--kind', 'ports', '--revision', 'v1'], 'ports')
run('audit_gate_annotation.py', ['--revision', 'v1'], 'annotation')
run('run_gate_timing_probe.py', ['--revision', 'v1', '--probe-revision', 'v1'], 'timing-negative', 600)
verify()
children = {}
streams = {}
state.update(status='GATE_NORMAL_AND_CORRUPT_RUNNING', 'parallel_stages': {})
for label, script in [('normal', 'run_gate.py'), ('corrupt', 'run_gate_corrupt.py')]:
    argv = [sys.executable, '-B', str(HERE / script), '--stage', label, '--revision', 'v1']
    stream = (case / (label + '.log')).open('w')
    child = subprocess.Popen(argv, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
                             start_new_session=True, preexec_fn=limits)
    children[label] = child
    streams[label] = stream
    state['parallel_stages'][label] = {'status': 'RUNNING', 'pid': child.pid, 'argv': argv}
    print('VECPIPE_GATE_WORKFLOW_LAUNCHED %s pid=%d' % (label, child.pid), flush=True)
save()
corrupt_reviewed = False
while any(item['status'] == 'RUNNING' for item in state['parallel_stages'].values()):
    for label, child in children.items():
        if state['parallel_stages'][label]['status'] == 'RUNNING' and child.poll() is not None:
            streams[label].close()
            state['parallel_stages'][label].update(status='TERMINAL', returncode=child.returncode)
            save()
            print('VECPIPE_GATE_WORKFLOW_TERMINAL %s returncode=%d' % (label, child.returncode), flush=True)
    if state['parallel_stages']['corrupt']['status'] == 'TERMINAL' and state['parallel_stages']['corrupt']['returncode'] == 0 and not corrupt_reviewed:
        run('audit_gate_corruption.py', [], 'corrupt-review')
        corrupt_reviewed = True
    if any(item['status'] == 'RUNNING' for item in state['parallel_stages'].values()):
        time.sleep(30)
        save()
assert all(item['returncode'] == 0 for item in state['parallel_stages'].values()), 'Inspect stage failure; existing other jobs were not stopped'
run('review_gate.py', ['--revision', 'v1', '--probe-revision', 'v1'], 'final-gate-review')
verify()
state['status'] = 'GATE_WORKFLOW_FINISHED_REVIEW_SCOPED_GATE_SUMMARY'
save()
(case / 'summary.json').write_text(json.dumps(state, indent=2) + '\n')
print(state['status'], flush=True)
