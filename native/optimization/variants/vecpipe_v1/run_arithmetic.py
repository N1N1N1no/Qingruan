"""Compare pipelined pointwise arithmetic to the verified scalar RTL.

Only unit equivalence/streaming are checked here. Cancellation/reset under
in-flight traffic and full AXI/SoC inference remain separate required gates.
"""
import hashlib
import json
import os
from pathlib import Path
import random
import re
import resource
import signal
import subprocess
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BASE = ROOT.parents[1]
TASK = BASE / 'native/audits/20261107/riscv_pe_contest/contest/task1'
case = ROOT / 'work/vecpipe-arithmetic-v1'
assert not case.exists(), 'Preserve prior attempt'
case.mkdir()


def local(path):
    return BASE / path[11:] if path.startswith('/workspace/') else Path(path)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


vectors = []
def add(op, a, b, c=0, d=0, zero=0):
    vectors.append((op, a & 0xffffffff, b & 0xffffffff, c & 0xffffffff, d & 0xffffffff, zero & 0xffffffff))


for i in range(256):
    add(6, i * 3 - 400, 0x3e000000, 0x40800000)
corners = [0, 0x80000000, 1, 0x80000001, 0x007fffff, 0x00800000, 0x3f800000,
           0xbf800000, 0x3eaaaaab, 0x3f000000, 0x7f7fffff, 0xff7fffff,
           0x7f800000, 0xff800000, 0x7fc12345, 0x7f812345, 0xffc12345]
for op in range(3):
    for a in corners:
        for b in corners:
            add(op, a, b)
scales = [0x3dcccccd, 0x3d000000, 0xbe4ccccd]
for op in [3, 4]:
    for a in range(256):
        for zero in range(256):
            add(op, a, scales[(a + zero) % len(scales)], zero=zero)
integers = [0, 1, -1, 0x7fffffff, 0x80000000, 0x00ffffff, 0x01000001, 0xfeffffff]
for op in [5, 6, 7]:
    for a in integers:
        for i, b in enumerate(corners):
            add(op, a, b, corners[(i + 3) % len(corners)], corners[(i + 7) % len(corners)])
rng = random.Random(0x713AC59D)
for i in range(16384):
    op = i % 10
    add(op, *(rng.getrandbits(32) for _ in range(4)), zero=0 if i % 3 else rng.getrandbits(32))
path = case / 'vectors.hex'
path.write_text(''.join('%x%08x%08x%08x%08x%08x\n' % vector for vector in vectors))
hf = ROOT / 'vendor/HardFloat-1/source'
files = list(dict.fromkeys([local(p) for p in json.loads((ROOT / 'hardfloat-files.json').read_text())] +
             [ROOT / 'rtl/fp32_vector.sv', HERE / 'fp32_vector_pipeline.sv', HERE / 'tb_vecpipe_arithmetic.sv']))
guards = {str(p): sha(p) for p in [Path(__file__), ROOT / 'hardfloat-files.json', path, *files]}
records = []


def run(argv, phase, limit):
    argv = list(map(str, argv))
    started = time.monotonic()
    print('VECPIPE_ARITHMETIC_PHASE ' + phase, flush=True)
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
                    print('VECPIPE_ARITHMETIC_RUNNING %s elapsed_s=%d' % (phase, time.monotonic() - started), flush=True)
                    if time.monotonic() - started > limit:
                        raise TimeoutError(phase)
        finally:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
                child.wait(timeout=10)
    records.append({'phase': phase, 'argv': argv, 'returncode': code, 'elapsed_s': time.monotonic() - started})
    assert code == 0, str(case / (phase + '.log'))
    return (case / (phase + '.log')).read_text(errors='replace')


run(['/usr/bin/gcc', '-fPIC', '-c', TASK / 'my_design/compat/pthread_yield_compat.c',
     '-o', case / 'compat.o'], 'compat', 120)
run(['/home/zhz/miniforge3/bin/python3', TASK / 'scripts/vcs_vm.py', '-full64', '-sverilog',
     '-timescale=1ns/1ps', '-assert', 'svaext', '+define+VCS', '+define+VECPIPE_CASES=' + str(len(vectors)),
     '+incdir+' + str(hf), '+incdir+' + str(hf / 'RISCV'), *files,
     '-top', 'tb_vecpipe_arithmetic', '-Mdir=' + str(case / 'csrc'), '-o', case / 'simv',
     '-LDFLAGS', case / 'compat.o'], 'compile', 600)
simulator = sha(case / 'simv')
text = run([case / 'simv', '+VECTORS=' + str(path)], 'normal', 1800)
assert text.count('VECPIPE_ARITHMETIC_PASS cases=' + str(len(vectors))) == 1
assert not re.search(r'Fatal:|Error-\[|VECPIPE_FAIL', text)
for name, expected in guards.items():
    assert sha(Path(name)) == expected, name
assert sha(case / 'simv') == simulator
result = {'status': 'VECPIPE_POINTWISE_BITS_FLAGS_ORDERING_AND_ONE_PER_CYCLE_POST_UNIT_PASS',
          'cases': len(vectors), 'all_S8_U8_byte_zero_pairs_checked': 131072,
          'POST_256_outputs_one_per_cycle': True, 'flags_errors_and_all32_result_bits_equal': True,
          'mixed_DIV_ordering_and_randomized_output_stalls': True, 'source_sha256': guards,
          'simulator_sha256': simulator, 'commands': records,
          'cancel_and_reset_inflight_accepted': False, 'AXI_integration_accepted': False,
          'full_model_inference_accepted': False, 'SoC_area_timing_accepted': False,
          'official_accuracy_accepted': False, 'final_ADP_accepted': False,
          'scope': 'Arithmetic unit only. Same original HardFloat RNE operations and every rounding point, with scalar DIV fallback. Exact scalar-RTL transaction equivalence, exhaustive byte/zero differences, float/int corners, random operands, output stalls, mixed-op ordering and sustained POST initiation1. Cancellation/reset, SRAM read-ahead, AXI/CPU integration, full inference and mapped PPA remain required.'}
(case / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
print(result['status'], flush=True)
