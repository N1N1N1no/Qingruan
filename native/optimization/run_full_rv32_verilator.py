"""Full RV32+all8 RTL using the already locally qualified Verilator path.

Reuse immutable VCS firmware/RAM/geometry. No host operator implementation.
Original VCS runs remain untouched and provide independent cycle comparison.
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

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--image', default='ILSVRC2012_val_00000274.JPEG')
parser.add_argument('--geometry', choices=['R8_C16_L4', 'R8_C32_L4', 'R16_C8_L4'], required=True)
parser.add_argument('--revision', default='v1')
args = parser.parse_args()
assert re.fullmatch(r'ILSVRC2012_val_\d{8}\.JPEG', args.image)
assert re.fullmatch(r'[A-Za-z0-9_-]+', args.revision)
fixture = ROOT / 'work/full-rv32' / args.geometry / args.image
qualified = ROOT / 'work/verilator-crosscheck'
case = ROOT / 'work/full-rv32-verilator' / args.geometry / args.image / args.revision
assert not case.exists(), 'Preserve previous attempt'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def local(path):
    return BASE / path[11:] if path.startswith('/workspace/') else Path(path)


def verify(hashes):
    for name, expected in hashes.items():
        assert sha(local(name)) == expected, name


def save(name, data):
    (case / name).write_text(json.dumps(data, indent=2) + '\n')


prep = json.loads((fixture / 'preparation.json').read_text())
qualification = json.loads((qualified / 'summary.json').read_text())
assert qualification['status'] == 'REAL_CPU_RTL_OUTPUT_AND_CYCLE_CROSSCHECK_PASS'
assert qualification['engine_trace_matches_VCS'] and qualification['CPU_and_call_cycles_match_VCS']
verify(prep['source_sha256'])
verify(qualification['source_sha256'])
assert prep['image'] == args.image
case.mkdir(parents=True)
for name in ['soc_accel_top.sv', 'tb_soc.sv']:
    (case / name).write_bytes((fixture / name).read_bytes())
ram = (fixture / 'axi_ram_model.sv').read_text()
assert ram.count('#0;') == 2
(case / 'axi_ram_model.sv').write_text(ram.replace('#0;', '#0.001;'))
monitor = ROOT / 'tests/full_model_monitor.sv'
(case / 'full_model_monitor.sv').write_bytes(monitor.read_bytes())
(case / 'rtl.f').write_bytes((qualified / 'rtl.f').read_bytes())
(case / 'full_top.sv').write_text('module full_top; tb_soc tb_soc(); full_model_monitor full_model_monitor(); endmodule\n')
replacements = {str(qualified / name): str(case / name) for name in
                ['soc_accel_top.sv', 'tb_soc.sv', 'axi_ram_model.sv', 'rtl.f']}
replacements.update({str(qualified / 'monitor.sv'): str(case / 'full_model_monitor.sv'),
                     str(qualified / 'crosscheck_top.sv'): str(case / 'full_top.sv'),
                     str(qualified / 'obj_dir'): str(case / 'obj_dir'),
                     'crosscheck_top': 'full_top', 'crosscheck_simv': 'full_simv'})
old_compile = qualification['commands_run'][0]['argv']
assert qualification['commands_run'][0]['phase'] == 'compile' and '-CFLAGS' not in old_compile
compile_argv = [replacements.get(value, value) for value in old_compile]
compile_argv.extend(['-CFLAGS', '-O3 -march=native'])
# The sole HardFloat syntactic adapter is exactly the already qualified copy.
div_adapter = qualified / 'divSqrtRecFN_small.v'
assert str(div_adapter) in compile_argv
hashes = dict(prep['source_sha256'])
for name, value in qualification['source_sha256'].items():
    assert name not in hashes or hashes[name] == value
    hashes[name] = value
paths = [Path(__file__), fixture / 'preparation.json', qualified / 'summary.json', div_adapter,
         *(case / name for name in ['soc_accel_top.sv', 'tb_soc.sv', 'axi_ram_model.sv',
                                    'full_model_monitor.sv', 'rtl.f', 'full_top.sv'])]
hashes.update({str(path): sha(path) for path in paths})
save('preparation.json', {'status': 'FULL_RV32_VERILATOR_FIXTURES_READY_NOT_INFERENCE_ACCEPTED',
     'geometry': args.geometry, 'image': args.image, 'VCS_fixture': str(fixture),
     'source_sha256': hashes, 'parameters': prep['parameters'], 'RAM_image_sha256': sha(fixture / 'data.hex'),
     'clock_period_ns': 10, 'RAM_response_delay_ns': 1, 'qualification': str(qualified / 'summary.json'),
     'compatibility_changes': ['Same qualified HardFloat redundant ANSI wire declaration removal.',
         'Only isolated RAM completion delta #0 to1ps; all ordinary RAM response timing and CPU cycles unchanged.'],
     'scope': 'Real complete RV32 CPU and all8 RTL, original VCS image/geometry/firmware/monitor. O3/native C++ compilation only changes simulator implementation. No host math, altered RTL datapath, timing acceptance or organizer accuracy claim.'})
package = ROOT / 'toolchain-cache/verilator-5.032/usr'
environment = dict(os.environ, VERILATOR_ROOT=str(package / 'share/verilator'))
records = []


def run(argv, phase, timeout):
    argv = list(map(str, argv))
    print('FULL_RV32_VERILATOR_PHASE ' + phase, flush=True)
    start = time.monotonic()

    def limits():
        resource.setrlimit(resource.RLIMIT_AS, (8 * 1024**3, 8 * 1024**3))

    with (case / (phase + '.log')).open('w') as stream:
        child = subprocess.Popen(argv, cwd=case, env=environment, stdout=stream,
                                 stderr=subprocess.STDOUT, start_new_session=True, preexec_fn=limits)
        save('live-process.json', {'status': 'RUNNING', 'phase': phase, 'pid': child.pid, 'argv': argv})
        try:
            while True:
                try:
                    code = child.wait(timeout=30)
                    break
                except subprocess.TimeoutExpired:
                    print('FULL_RV32_VERILATOR_RUNNING %s elapsed_s=%d' % (phase, time.monotonic() - start), flush=True)
                    if time.monotonic() - start > timeout:
                        raise TimeoutError(phase)
        finally:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait(timeout=10)
            save('live-process.json', {'status': 'TERMINAL', 'phase': phase, 'returncode': child.returncode})
    records.append({'phase': phase, 'argv': argv, 'returncode': code, 'elapsed_s': time.monotonic() - start})
    save(phase + '-command.json', records[-1])
    return code, (case / (phase + '.log')).read_text(errors='replace')


code, _ = run(compile_argv, 'compile', 1800)
assert code == 0, str(case / 'compile.log')
verify(hashes)
executable = case / 'obj_dir/full_simv'
simulator_hash = sha(executable)
save('compile-summary.json', {'status': 'FULL_RV32_VERILATOR_COMPILED_NOT_INFERENCE_ACCEPTED',
     'simulator_sha256': simulator_hash, 'command': records[-1], 'preparation_sha256': sha(case / 'preparation.json')})
code, text = run(['stdbuf', '-oL', '-eL', executable, '+MEM_HEX=' + str(fixture / 'data.hex'),
                  '+MAX_CYCLES=6000000000', '+REPORT=' + str(case / 'report.txt'),
                  '+LOGITS=' + str(case / 'logits.bin'),
                  *('+' + key + '=' + value for key, value in prep['parameters'].items())], 'normal', 86400)
verify(hashes)
assert sha(executable) == simulator_hash
passed = code == 0 and 'FULL_MODEL_RV32_PASS' in text and 'FULL_MODEL_CPU_RETURN_PASS' in text and not re.search(
    r'(?i)Fatal:|Error-\[|FULL_MODEL_FAIL|AXI_RX_(?:SETUP_FAIL|UNKNOWN)', text)
result = {'status': 'FULL_RV32_SINGLE_IMAGE_FROZEN_C_PASS_OFFICIAL_ACCURACY_OPEN' if passed else 'FULL_RV32_VERILATOR_DIAGNOSTIC_FAILURE',
          'geometry': args.geometry, 'image': args.image, 'simulator_kind': 'Verilator5.032',
          'full_RV32_executed': True, 'all8_engines_participation_asserted': passed,
          'source_sha256': hashes, 'simulator_sha256': simulator_hash, 'commands': records,
          'VCS_fixture': str(fixture), 'VCS_full_model_cycle_equality_accepted': False,
          'official_accuracy_accepted': False, 'final_SoC_ADP_accepted': False,
          'scope': 'Actual full RV32+all8 RTL, same frozen VCS firmware/data/geometry and complete monitor. Internal full1000-logit frozen-C check only; formal organizer accuracy, gate timing and VCS full-model comparison remain separate gates.'}
if (case / 'report.txt').exists():
    result['report'] = (case / 'report.txt').read_text()
if (case / 'logits.bin').exists():
    result['logits_sha256'] = sha(case / 'logits.bin')
    expected = ROOT / 'work/fusion-proof' / (args.image + '.baseline')
    result['logits_byte_exact'] = (case / 'logits.bin').read_bytes() == expected.read_bytes()
save('summary.json' if passed else 'failure.json', result)
assert passed, str(case / 'failure.json')
print(result['status'], flush=True)
