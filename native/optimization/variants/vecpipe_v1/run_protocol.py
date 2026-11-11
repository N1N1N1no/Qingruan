"""Targeted cancellation/reset checks after arithmetic streaming qualification."""
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
original = ROOT / 'work/vecpipe-arithmetic-v1'
case = ROOT / 'work/vecpipe-protocol-v1'
assert not case.exists(), 'Preserve prior attempt'


def local(path):
    return BASE / path[11:] if path.startswith('/workspace/') else Path(path)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


proof = json.loads((original / 'summary.json').read_text())
assert proof['status'] == 'VECPIPE_POINTWISE_BITS_FLAGS_ORDERING_AND_ONE_PER_CYCLE_POST_UNIT_PASS'
for name, expected in proof['source_sha256'].items():
    assert sha(local(name)) == expected, name
case.mkdir()
(case / 'compat.o').write_bytes((original / 'compat.o').read_bytes())
compile_argv = next(record['argv'] for record in proof['commands'] if record['phase'] == 'compile')
compile_argv = [argument.replace(str(original), str(case)).replace('tb_vecpipe_arithmetic', 'tb_vecpipe_protocol')
                for argument in compile_argv if not argument.startswith('+define+VECPIPE_CASES=')]
paths = [Path(__file__), HERE / 'tb_vecpipe_protocol.sv', original / 'summary.json', case / 'compat.o']
hashes = dict(proof['source_sha256'])
hashes.update({str(path): sha(path) for path in paths})
records = []


def run(argv, phase, limit):
    argv = list(map(str, argv))
    print('VECPIPE_PROTOCOL_PHASE ' + phase, flush=True)
    started = time.monotonic()
    def memory_limit():
        resource.setrlimit(resource.RLIMIT_AS, (8 * 1024**3, 8 * 1024**3))
    with (case / (phase + '.log')).open('w') as stream:
        child = subprocess.Popen(argv, cwd=case, stdout=stream, stderr=subprocess.STDOUT,
                                 start_new_session=True, preexec_fn=memory_limit)
        try:
            while True:
                try:
                    code = child.wait(timeout=30)
                    break
                except subprocess.TimeoutExpired:
                    print('VECPIPE_PROTOCOL_RUNNING ' + phase, flush=True)
                    if time.monotonic() - started > limit:
                        raise TimeoutError(phase)
        finally:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
                child.wait(timeout=10)
    records.append({'phase': phase, 'argv': argv, 'returncode': code, 'elapsed_s': time.monotonic() - started})
    assert code == 0, str(case / (phase + '.log'))
    return (case / (phase + '.log')).read_text(errors='replace')


run(compile_argv, 'compile', 600)
simulator = sha(case / 'simv')
text = run([case / 'simv'], 'normal', 120)
assert text.count('VECPIPE_PROTOCOL_PASS checked=') == 1
assert not re.search(r'Fatal:|Error-\[|VECPIPE_PROTOCOL_FAIL', text)
for name, expected in hashes.items():
    assert sha(local(name)) == expected, name
assert sha(case / 'simv') == simulator
result = {'status': 'VECPIPE_INFLIGHT_CANCEL_VISIBLE_RESULT_AND_RESET_PROTOCOL_UNIT_PASS',
          'cancel_single_fast_request': True, 'cancel_inflight_group': True,
          'visible_stalled_result_preserved_during_cancel': True,
          'iterative_DIV_cancel_drained_before_recovery': True,
          'fast_and_DIV_reset_recovery': True, 'source_sha256': hashes,
          'simulator_sha256': simulator, 'commands': records,
          'AXI_integration_accepted': False, 'full_model_inference_accepted': False,
          'SoC_area_timing_accepted': False, 'official_accuracy_accepted': False,
          'scope': 'Targeted arithmetic-unit protocol checks. Cancelling a fast group discards not-yet-visible work with one cancellation response, preserves an already-visible result until acknowledgement, drains scalar DIV, and recovers after fast/DIV reset. SRAM/AXI/real-CPU integration and mapped PPA/full-model checks remain required.'}
(case / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
print(result['status'], flush=True)
