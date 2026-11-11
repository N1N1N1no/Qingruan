"""Same first image and exact existing firmware; actual RV32 streaming variant.

Copies the completed scalar fixture, changes only the backend module type and
three RTL sources, and retains every original full-model monitor assertion.
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
parser.add_argument('--stage', choices=['prepare', 'run', 'all'], default='all')
args = parser.parse_args()
assert re.fullmatch(r'[A-Za-z0-9_-]+', args.revision)
case = ROOT / ('work/vecpipe-full-rv32-' + args.revision)
original = ROOT / 'work/full-rv32/R8_C16_L4/ILSVRC2012_val_00000274.JPEG'


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


def mapped(name):
    def moved(value):
        return str(local(value)).replace(str(original), str(case))
    if name.startswith('/'):
        return moved(name)
    if name.startswith('+incdir+'):
        return '+incdir+' + str(local(name[len('+incdir+'):]))
    if '=' in name:
        key, value = name.split('=', 1)
        if value.startswith('/'):
            return key + '=' + moved(value)
    return name


if args.stage in ['prepare', 'all']:
    assert not case.exists(), 'Preserve prior attempt; use a new revision'
    old = json.loads((original / 'summary.json').read_text())
    assert old['status'] == 'FULL_RV32_SINGLE_IMAGE_FROZEN_C_PASS_OFFICIAL_ACCURACY_OPEN'
    assert old['logits_byte_exact'] and old['all8_engines_participation_asserted']
    verify(old['source_sha256'])
    assert sha(original / 'simv') == old['simulator_sha256']
    oldprep = json.loads((original / 'preparation.json').read_text())
    assert oldprep['source_sha256'] == old['source_sha256']
    axi_path = ROOT / 'work/vecpipe-axi-v1/summary.json'
    axi = json.loads(axi_path.read_text())
    assert axi['status'] == 'VECPIPE_AXI_REFERENCE_FAULT_CANCEL_RESET_THREE_SRAM_GEOMETRIES_PASS'
    verify(axi['source_sha256'])
    backend_generation = json.loads((HERE / 'backend-generation.json').read_text())
    assert sha(HERE / 'axi_accel_backend_vecpipe.sv') == backend_generation['generated_sha256']
    assert sha(ROOT / 'rtl/axi_accel_shared_backend.sv') == backend_generation['original_sha256']
    case.mkdir()
    names = ['program.elf', 'program.bin', 'program.hex', 'data.hex', 'soc_accel_top.sv', 'tb_soc.sv', 'axi_ram_model.sv']
    for name in names:
        (case / name).write_bytes((original / name).read_bytes())
    top = case / 'soc_accel_top.sv'
    text = top.read_text()
    assert text.count('axi_accel_backend #(') == 1
    top.write_text(text.replace('axi_accel_backend #(', 'axi_accel_backend_vecpipe #('))
    compile_argv = [mapped(name) for name in next(item['argv'] for item in old['commands'] if item['phase'] == 'compile')]
    for old_name, new_path in [('axi_vector_engine.sv', HERE / 'axi_vector_engine_vecpipe.sv'),
                               ('axi_accel_shared_backend.sv', HERE / 'axi_accel_backend_vecpipe.sv')]:
        name = str(ROOT / 'rtl' / old_name)
        assert compile_argv.count(name) == 1
        compile_argv[compile_argv.index(name)] = str(new_path)
    compile_argv += [str(HERE / 'fp32_vector_pipeline.sv')]
    commands = {phase: [mapped(name) for name in next(item['argv'] for item in old['commands'] if item['phase'] == phase)]
                for phase in ['compat', 'normal']}
    commands['compile'] = compile_argv
    paths = [Path(__file__), original / 'summary.json', original / 'preparation.json',
             original / 'logits.bin', original / 'report.txt', axi_path,
             HERE / 'backend-generation.json', HERE / 'axi_accel_backend_vecpipe.sv',
             HERE / 'axi_vector_engine_vecpipe.sv', HERE / 'fp32_vector_pipeline.sv',
             *[local(name) for name in old['source_sha256']], *[case / name for name in names]]
    hashes = {str(path): sha(path) for path in dict.fromkeys(paths)}
    identical = {name: sha(case / name) == sha(original / name) for name in names if name != 'soc_accel_top.sv'}
    assert all(identical.values())
    prep = {'status': 'VECPIPE_REAL_RV32_FULL_MODEL_PREPARED_NOT_EXECUTED',
            'image': old['image'], 'geometry': 'R8_C16_L4', 'source_sha256': hashes,
            'parameters': oldprep['parameters'], 'sections': oldprep['sections'],
            'unchanged_fixture_files_byte_exact': identical, 'argv': commands,
            'expected_logits_sha256': sha(original / 'logits.bin'),
            'original_report': old['report'], 'clock_period_ns': 10,
            'RAM_response_delay_ns': 1, 'SRAM_macros': 128,
            'scope': 'Same firmware, model, input, lookup tables, frozen reference, external RAM and timing monitor. Only vector pipeline/operand FIFO and backend module type differ.'}
    (case / 'preparation.json').write_text(json.dumps(prep, indent=2) + '\n')
    if args.stage == 'prepare':
        print(prep['status'], flush=True)
        raise SystemExit(0)
else:
    prep = json.loads((case / 'preparation.json').read_text())

assert not (case / 'normal.log').exists() and not (case / 'summary.json').exists(), 'Preserve started attempt'
verify(prep['source_sha256'])
soc_path = ROOT / 'work/vecpipe-soc-v1/summary.json'
soc = json.loads(soc_path.read_text())
assert soc['status'] == 'VECPIPE_REAL_RV32_ALL8_SHARED_BACKEND_FROZEN_FIRMWARE_AND_CPU_NEGATIVE_PASS'
verify(soc['source_sha256'])
hashes = dict(prep['source_sha256'])
hashes.update({str(soc_path): sha(soc_path), str(case / 'preparation.json'): sha(case / 'preparation.json')})
records = []


def run(argv, label, deadline):
    argv = list(map(str, argv))
    print('VECPIPE_FULL_RV32_PHASE ' + label, flush=True)
    started = time.monotonic()
    def memory_limit():
        resource.setrlimit(resource.RLIMIT_AS, (8 * 1024**3, 8 * 1024**3))
    with (case / (label + '.log')).open('w') as log:
        process = subprocess.Popen(argv, cwd=case, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True, preexec_fn=memory_limit)
        (case / 'live-process.json').write_text(json.dumps({'status': 'RUNNING', 'phase': label,
                                                          'pid': process.pid, 'argv': argv}, indent=2) + '\n')
        try:
            while True:
                try:
                    code = process.wait(timeout=30)
                    break
                except subprocess.TimeoutExpired:
                    print('VECPIPE_FULL_RV32_RUNNING %s elapsed_s=%d' % (label, time.monotonic() - started), flush=True)
                    if time.monotonic() - started > deadline:
                        raise TimeoutError(label)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=10)
            (case / 'live-process.json').write_text(json.dumps({'status': 'TERMINAL', 'phase': label,
                                                              'returncode': process.returncode}, indent=2) + '\n')
    records.append({'phase': label, 'argv': argv, 'returncode': code, 'elapsed_s': time.monotonic() - started})
    return code, (case / (label + '.log')).read_text(errors='replace')


assert run(prep['argv']['compat'], 'compat', 120)[0] == 0
assert run(prep['argv']['compile'], 'compile', 1800)[0] == 0
simulator = sha(case / 'simv')
code, normal = run(prep['argv']['normal'], 'normal', 86400)
verify(hashes)
assert sha(case / 'simv') == simulator
passed = (code == 0 and normal.count('FULL_MODEL_RV32_PASS') == 1 and
          normal.count('FULL_MODEL_CPU_RETURN_PASS') == 1 and
          not re.search(r'Fatal:|Error-\[|FULL_MODEL_FAIL|AXI_RX_(?:SETUP_FAIL|UNKNOWN)|VECPIPE_FIFO_FAIL', normal))
result = {'status': 'VECPIPE_REAL_RV32_FULL_FIRST_IMAGE_BYTE_EXACT_PASS_OFFICIAL_ACCURACY_OPEN' if passed else 'VECPIPE_REAL_RV32_FULL_MODEL_DIAGNOSTIC_FAILURE',
          'full_RV32_executed': True, 'image': prep['image'], 'geometry': prep['geometry'],
          'source_sha256': hashes, 'simulator_sha256': simulator, 'commands': records,
          'official_accuracy_accepted': False, 'final_SoC_ADP_accepted': False,
          'scope': 'Actual RV32+all8 complete image inference with same binary firmware and complete C reference/guard checks. Whole-model numeric preservation and complete-cycle comparison only; official accuracy, mapped PPA and final selection remain separate.'}
if (case / 'report.txt').exists():
    report = (case / 'report.txt').read_text()
    result['report'] = report
    match = re.search(r'inference_cycles=(\d+) CPU_return_cycle=(\d+)', report)
    if match:
        result['inference_cycles'], result['CPU_return_cycle'] = map(int, match.groups())
        old_cycles = int(re.search(r'inference_cycles=(\d+)', prep['original_report'])[1])
        result['scalar_full_inference_cycles'] = old_cycles
        result['full_inference_cycle_ratio'] = result['inference_cycles'] / old_cycles
        result['full_inference_speedup'] = old_cycles / result['inference_cycles']
if (case / 'logits.bin').exists():
    result['logits_sha256'] = sha(case / 'logits.bin')
    result['logits_byte_exact_to_completed_scalar_and_frozen_C'] = result['logits_sha256'] == prep['expected_logits_sha256']
    passed = passed and result['logits_byte_exact_to_completed_scalar_and_frozen_C']
else:
    passed = False
if not passed:
    result['status'] = 'VECPIPE_REAL_RV32_FULL_MODEL_DIAGNOSTIC_FAILURE'
(case / ('summary.json' if passed else 'failure.json')).write_text(json.dumps(result, indent=2) + '\n')
assert passed, str(case / 'failure.json')
print(result['status'], flush=True)
