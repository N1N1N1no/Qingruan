"""Same frozen R8_C16 firmware and all8 RV32 integration, new vector only."""
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
args = parser.parse_args()
case = ROOT / ('work/vecpipe-soc-' + args.revision)
assert not case.exists(), 'Preserve prior attempt; use a new revision'
case.mkdir()
original = ROOT / 'work/complete-soc-R8_C16_L4/shared'


def local(name):
    for prefix in ['/workspace/', '/mnt/data/Ubuntu/qingruan/']:
        if name.startswith(prefix):
            return BASE / name[len(prefix):]
    return Path(name)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


baseline = json.loads((original / 'summary.json').read_text())
assert baseline['status'] == 'PASS' and baseline['shared_FP32']
for name, expected in baseline['source_sha256'].items():
    assert sha(local(name)) == expected, name
for name, expected in baseline['fixture_sha256'].items():
    assert sha(original / name) == expected, name
    (case / name).write_bytes((original / name).read_bytes())
qualification = ROOT / 'work/vecpipe-axi-v1/summary.json'
axi = json.loads(qualification.read_text())
assert axi['status'] == 'VECPIPE_AXI_REFERENCE_FAULT_CANCEL_RESET_THREE_SRAM_GEOMETRIES_PASS'
for name, expected in axi['source_sha256'].items():
    assert sha(local(name)) == expected, name
generation = json.loads((HERE / 'backend-generation.json').read_text())
for path, key in [(ROOT / 'rtl/axi_accel_shared_backend.sv', 'original_sha256'),
                  (HERE / 'axi_accel_backend_vecpipe.sv', 'generated_sha256'),
                  (HERE / 'create_backend.py', 'generator_sha256')]:
    assert sha(path) == generation[key]
top = case / 'soc_accel_top.sv'
text = top.read_text()
assert text.count('axi_accel_backend #(') == 1
top.write_text(text.replace('axi_accel_backend #(', 'axi_accel_backend_vecpipe #('))


def argument(name):
    return name.replace(str(original), str(case))


# Stored argv may use either mounted workspace spelling.
def mapped(name):
    if name.startswith('/'):
        return argument(str(local(name)))
    if name.startswith('+incdir+'):
        return '+incdir+' + str(local(name[len('+incdir+'):]))
    if '=' in name:
        key, value = name.split('=', 1)
        if value.startswith('/'):
            return key + '=' + argument(str(local(value)))
    return name


compile_argv = [mapped(name) for name in next(item['argv'] for item in baseline['commands'] if item['label'] == 'compile')]
old_backend = str(ROOT / 'rtl/axi_accel_shared_backend.sv')
old_engine = str(ROOT / 'rtl/axi_vector_engine.sv')
assert compile_argv.count(old_backend) == 1 and compile_argv.count(old_engine) == 1
compile_argv[compile_argv.index(old_backend)] = str(HERE / 'axi_accel_backend_vecpipe.sv')
compile_argv[compile_argv.index(old_engine)] = str(HERE / 'axi_vector_engine_vecpipe.sv')
compile_argv += [str(HERE / 'fp32_vector_pipeline.sv')]
sources = [Path(name) for name in compile_argv if name.endswith(('.sv', '.v'))]
paths = [Path(__file__), original / 'summary.json', qualification, HERE / 'backend-generation.json',
         HERE / 'create_backend.py', ROOT / 'run_complete_soc.py', *sources,
         *[case / name for name in baseline['fixture_sha256']]]
hashes = {str(path): sha(path) for path in paths}
records = []


def run(argv, label, limit=1800):
    argv = list(map(str, argv))
    print('VECPIPE_SOC_PHASE ' + label, flush=True)
    started = time.monotonic()
    def memory_limit():
        resource.setrlimit(resource.RLIMIT_AS, (8 * 1024**3, 8 * 1024**3))
    with (case / (label + '.log')).open('w') as stream:
        child = subprocess.Popen(argv, cwd=case, stdout=stream, stderr=subprocess.STDOUT,
                                 start_new_session=True, preexec_fn=memory_limit)
        try:
            while True:
                try:
                    code = child.wait(timeout=30)
                    break
                except subprocess.TimeoutExpired:
                    print('VECPIPE_SOC_RUNNING %s elapsed_s=%d' % (label, time.monotonic() - started), flush=True)
                    if time.monotonic() - started > limit:
                        raise TimeoutError(label)
        finally:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
                child.wait(timeout=10)
    records.append({'label': label, 'argv': argv, 'returncode': code, 'elapsed_s': time.monotonic() - started})
    return code, (case / (label + '.log')).read_text(errors='replace')


compat = [mapped(name) for name in next(item['argv'] for item in baseline['commands'] if item['label'] == 'compat')]
assert run(compat, 'compat', 120)[0] == 0
assert run(compile_argv, 'compile', 1800)[0] == 0
simulator = sha(case / 'simv')
argv = [mapped(name) for name in next(item['argv'] for item in baseline['commands'] if item['label'] == 'normal')]
code, normal = run(argv, 'normal')
assert code == 0 and normal.count('COMPLETE_SOC_PASS commands=22') == 1 and normal.count('COMPLETE_SOC_CPU_RETURN_PASS') == 1
assert not re.search(r'(?im)^\s*(?:Warning|Lint|Error|Fatal)(?:-|:)|AXI_RX_(?:SETUP_FAIL|UNKNOWN)|VECPIPE_(?:FIFO|AXI)_FAIL|COMPLETE_SOC_FAIL|TIMEOUT', normal)
normal_report = (case / 'report.txt').read_bytes()
code, negative = run([*argv, '+INJECT_VECTOR_ERROR'], 'negative')
# Only the unique intentional RAM-model failure return is accepted.
ram = case / 'axi_ram_model.sv'
sites = [i + 1 for i, line in enumerate(ram.read_text().splitlines())
         if '$fatal(1,"COMPLETE_SOC_CPU_RETURN_FAIL code=%08x",done_value)' in line]
assert len(sites) == 1
pattern = r'(?m)^Fatal: "([^"]+)", (\d+): tb_soc\.dmem\.core: at time (\d+) (ps|fs)\nCOMPLETE_SOC_CPU_RETURN_FAIL code=bad00120$'
matches = list(re.finditer(pattern, negative))
assert len(matches) == 1
match = matches[0]
assert local(match[1]).resolve() == ram.resolve() and int(match[2]) == sites[0] and int(match[3]) > 0
checked = negative[:match.start()] + negative[match.end():]
assert negative.count('COMPLETE_SOC_NEGATIVE_PASS CPU_detected_corrupted_byte=7') == 1
assert negative.count('COMPLETE_SOC_CPU_RETURN_FAIL code=bad00120') == 1
assert 'COMPLETE_SOC_CPU_RETURN_PASS' not in negative
assert not re.search(r'(?im)^\s*(?:Warning|Lint|Error|Fatal)(?:-|:)|AXI_RX_(?:SETUP_FAIL|UNKNOWN)|VECPIPE_(?:FIFO|AXI)_FAIL|COMPLETE_SOC_FAIL|TIMEOUT', checked)
assert (case / 'report.txt').read_bytes() == normal_report
trace = [list(map(int, match.groups())) for match in re.finditer(r'COMPLETE_SOC_ENGINE_DONE command=(\d+) cycles=(\d+) bytes=(\d+) error=(\d+)', normal)]
assert len(trace) == 22
opcodes = [int(match[1]) for match in re.finditer(r'COMPLETE_SOC_COMMAND number=\d+ opcode=(\d+)', normal)]
assert len(opcodes) == 22 and set(opcodes) == set(range(1, 9))
for old, new, op in zip(baseline['engine_trace'], trace, opcodes):
    assert old[0] == new[0] and old[2:] == new[2:]
    if op != 7:
        assert old == new, (op, old, new)
for name, expected in hashes.items():
    assert sha(Path(name)) == expected, name
assert sha(case / 'simv') == simulator
assert sha(case / 'program.bin') == sha(original / 'program.bin')
summary = {'status': 'VECPIPE_REAL_RV32_ALL8_SHARED_BACKEND_FROZEN_FIRMWARE_AND_CPU_NEGATIVE_PASS',
           'geometry': 'R8_C16_L4', 'shared_FP32_DIV': True, 'clock_period_ns': 10,
           'RAM_response_delay_ns': 1, 'SRAM_macros': 128, 'all8_engines_participated': True,
           'unchanged_firmware_bytes': (case / 'program.bin').stat().st_size,
           'firmware_sha256': sha(case / 'program.bin'), 'engine_trace': trace,
           'vector_command_comparisons': [{'command': old[0], 'scalar_cycles': old[1], 'pipeline_cycles': new[1]}
                                          for old, new, op in zip(baseline['engine_trace'], trace, opcodes) if op == 7],
           'source_sha256': hashes, 'simulator_sha256': simulator, 'commands': records,
           'negative_control_detected': True, 'CPU_detected_corrupted_byte_index_zero_based': 7,
           'intentional_fatal': {'file': str(ram), 'line': sites[0], 'return_code': 'bad00120', 'occurrences': 1},
           'full_model_inference_accepted': False, 'SoC_area_timing_accepted': False,
           'official_accuracy_accepted': False, 'final_ADP_accepted': False,
           'scope': 'Actual RV32 same frozen all8 22-command firmware, same original SRAM and RAM timing; CPU checks every output and guards. Unchanged non-vector engine cycles; byte corruption detected by actual CPU. No whole-model inference, synthesis timing or final acceptance from micrograph.'}
(case / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
print(summary['status'], flush=True)
