"""Real original CPU+PE VCS baseline; isolated prefix mode is not full inference.

Uses unmodified original arithmetic RTL, same firmware/RAM/timing/monitor.
The existing Verilator fixture qualification guards the copied RAM/TB inputs.
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
TASK = BASE / 'native/audits/20261107/riscv_pe_contest/contest/task1'
PROJECT = TASK / 'my_design/project'
parser = argparse.ArgumentParser()
parser.add_argument('--image', default='ILSVRC2012_val_00000274.JPEG')
parser.add_argument('--revision', default='v1')
parser.add_argument('--stage', choices=['prepare', 'compile', 'run', 'all'], default='all')
parser.add_argument('--prefix-cycles', type=int, default=0)
parser.add_argument('--wall-limit-s', type=int, default=604800)
args = parser.parse_args()
assert re.fullmatch(r'ILSVRC2012_val_\d{8}\.JPEG', args.image)
assert re.fullmatch(r'[A-Za-z0-9_-]+', args.revision)
assert 0 <= args.prefix_cycles < 1000000000000 and 60 <= args.wall_limit_s <= 2592000
case = ROOT / 'work/original-baseline-rtl/vcs' / args.revision
firmware = ROOT / 'work/original-baseline-full' / args.image
qualified = ROOT / 'work/original-baseline-verilator-crosscheck-v4'
package = ROOT / 'toolchain-cache/verilator-5.032/usr'
environment = dict(os.environ, VERILATOR_ROOT=str(package / 'share/verilator'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def local(path):
    return BASE / path[11:] if path.startswith('/workspace/') else BASE / path[len('/mnt/data/Ubuntu/qingruan/'):] if path.startswith('/mnt/data/Ubuntu/qingruan/') else Path(path)


def verify(hashes):
    for name, expected in hashes.items():
        assert sha(local(name)) == expected, name


def save(name, obj):
    (case / name).write_text(json.dumps(obj, indent=2) + '\n')


if args.stage in ['prepare', 'all']:
    assert not case.exists(), 'Preserve previous attempt; use a fresh revision'
    prep = json.loads((firmware / 'preparation.json').read_text())
    qualification = json.loads((qualified / 'summary.json').read_text())
    assert prep['image'] == args.image and prep['unmodified_graph_and_PE_driver_C']
    assert prep['SWIN_HOST_not_defined'] and not prep['baseline_total_cycles_measured']
    assert qualification['status'] == 'ORIGINAL_CPU_PE_VERILATOR_VCS_COMPLETE_TRANSACTION_TRACE_AND_CYCLES_PASS'
    assert qualification['complete_accepted_transaction_trace_byte_identical']
    assert qualification['formally_proved_combinational_adapter_used']
    verify(prep['source_sha256'])
    verify({str(firmware / name): value for name, value in prep['artifact_sha256'].items()})
    verify(qualification['source_sha256'])
    case.mkdir(parents=True)
    (case / 'rtl.f').write_bytes((PROJECT / 'build/rtl.f').read_bytes())
    tb = (qualified / 'tb_soc.sv').read_text()
    assert tb.count('integer max_cycles = 200000;') == 1 and tb.count('integer cycles = 0;') == 1
    tb = tb.replace('integer max_cycles = 200000;', "longint unsigned max_cycles = 64'd1000000000000;")
    tb = tb.replace('integer cycles = 0;', 'longint unsigned cycles = 0;')
    optional_wave = '        if ($test$plusargs("WAVE")) begin\n`ifdef VCS\n            $vcdplusfile("build/soc.vpd");\n            $vcdpluson(0, tb_soc);\n`else\n            $dumpfile("build/soc.vcd");\n            $dumpvars(0, tb_soc);\n`endif\n        end\n'
    assert tb.count(optional_wave) == 1
    # This isolated speed benchmark never requests WAVE. Optional VPD system
    # tasks require debug instrumentation even in a runtime-disabled branch.
    tb = tb.replace(optional_wave, '')
    (case / 'tb_soc.sv').write_text(tb)
    ram = (qualified / 'axi_ram_model.sv').read_text()
    assert ram.count('parameter MEM_BYTES=262144') == 2
    assert ram.count('SOC_SMOKE_PASS: CPU checked all 64 PE output words') == 1
    assert ram.count('else $fatal(1,"SOC_SMOKE_FAIL code=%08x",done_value);') == 1
    ram = ram.replace('parameter MEM_BYTES=262144', 'parameter MEM_BYTES=67108864')
    ram = ram.replace('SOC_SMOKE_PASS: CPU checked all 64 PE output words', 'ORIGINAL_BASELINE_CPU_RETURN_PASS')
    ram = ram.replace('else $fatal(1,"SOC_SMOKE_FAIL code=%08x",done_value);',
                      'else begin #0.001; $fatal(1,"ORIGINAL_BASELINE_CPU_FAILURE code=%08x",done_value); end')
    (case / 'axi_ram_model.sv').write_text(ram)
    for source, name in [(qualified / 'reference_sram.v', 'reference_sram.v'),
                         (ROOT / 'tests/original_baseline_full_monitor.sv', 'original_baseline_full_monitor.sv')]:
        (case / name).write_bytes(source.read_bytes())
    (case / 'baseline_top.sv').write_text('module baseline_top; tb_soc tb_soc(); original_baseline_full_monitor original_baseline_full_monitor(); endmodule\n')
    variables = {'NODE_CYCLES': 'node_cycles', 'INFERENCE_CYCLES': 'inference_cycles',
                 'ERROR_NODE': 'model_error_node', 'MAC_JOBS': 'model_mac_jobs', 'TOP1': 'model_top1',
                 'EXPECTED_TOP1': 'model_expected_top1', 'MAX_ERROR': 'model_max_error_bits',
                 'WEIGHT_LOADS': 'baseline_weight_loads', 'PE_PASSES': 'baseline_pe_passes',
                 'MMIO_WRITES': 'baseline_mmio_writes', 'MMIO_READS': 'baseline_mmio_reads'}
    parameters = {name: '%x' % prep['symbols'][symbol] for name, symbol in variables.items()}
    parameters.update({key: '%x' % prep[field] for key, field in [('ARENA_LO', 'arena_lo'),
                       ('ARENA_HI', 'arena_hi'), ('OUTPUT_ADDR', 'output_address'), ('EXPECTED_ADDR', 'expected_address')]})
    parameters.update({key: str(prep['static_original_driver_counts'][field]) for key, field in [
        ('EXPECTED_LOADS', 'weight_loads'), ('EXPECTED_PASSES', 'pe_passes'),
        ('EXPECTED_WRITES', 'mmio_writes'), ('EXPECTED_READS', 'mmio_reads')]})
    hashes = dict(qualification['source_sha256'])
    hashes.update(prep['source_sha256'])
    hashes.update({str(firmware / name): value for name, value in prep['artifact_sha256'].items()})
    files = [Path(__file__), PROJECT / 'build/rtl.f', PROJECT / 'tb/sram_reference_model.v', TASK / 'scripts/vcs_vm.py', TASK / 'my_design/compat/pthread_yield_compat.c', firmware / 'preparation.json', qualified / 'summary.json',
             ROOT / 'tests/original_baseline_full_monitor.sv',
             *(case / name for name in ['rtl.f', 'tb_soc.sv', 'axi_ram_model.sv', 'reference_sram.v',
                                       'original_baseline_full_monitor.sv', 'baseline_top.sv'])]
    hashes.update({str(path): sha(path) for path in files})
    save('preparation.json', {'status': 'ORIGINAL_FULL_CPU_PE_RTL_FIXTURES_PREPARED_NOT_EXECUTED',
         'image': args.image, 'firmware': str(firmware), 'parameters': parameters, 'source_sha256': hashes,
         'clock_period_ns': 10, 'RAM_response_delay_ns': 1, 'logical_external_RAM_bytes': 67108864,
         'qualification': str(qualified / 'summary.json'), 'full_model_accepted': False,
         'scope': 'Original unfused graph, unchanged original operator and MMIO PE C, real CPU/router/original256SRAM PE. Isolated 64-bit simulation counters and64MiB RAM. Unmodified original gate arithmetic RTL in VCS; no register/control/clock changes.'})
    print('ORIGINAL_BASELINE_FULL_RTL_PREPARED', flush=True)
if args.stage == 'prepare':
    raise SystemExit(0)
preparation = json.loads((case / 'preparation.json').read_text())
assert preparation['image'] == args.image
verify(preparation['source_sha256'])
records = []


def run(argv, phase, timeout):
    argv = list(map(str, argv))
    assert not (case / (phase + '.log')).exists(), 'Preserve prior run'
    start = time.monotonic()
    print('ORIGINAL_BASELINE_PHASE ' + phase, flush=True)

    def limits():
        resource.setrlimit(resource.RLIMIT_AS, (8 * 1024**3, 8 * 1024**3))

    with (case / (phase + '.log')).open('w') as log:
        process = subprocess.Popen(argv, cwd=case, env=environment, stdout=log,
                                   stderr=subprocess.STDOUT, preexec_fn=limits, start_new_session=True)
        save('live-process.json', {'status': 'RUNNING', 'phase': phase, 'pid': process.pid,
                                  'argv': argv, 'wall_limit_s': timeout, 'started_unix': time.time()})
        try:
            while True:
                try:
                    code = process.wait(timeout=30)
                    break
                except subprocess.TimeoutExpired:
                    print('ORIGINAL_BASELINE_RUNNING %s elapsed_s=%d' % (phase, time.monotonic() - start), flush=True)
                    if time.monotonic() - start > timeout:
                        raise TimeoutError(phase)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=10)
            save('live-process.json', {'status': 'TERMINAL', 'phase': phase, 'returncode': process.returncode})
    records.append({'phase': phase, 'returncode': code, 'elapsed_s': time.monotonic() - start, 'argv': argv})
    save(phase + '-command.json', records[-1])
    return code, (case / (phase + '.log')).read_text(errors='replace')


executable = case / 'simv'
if args.stage in ['compile', 'all']:
    assert not executable.exists(), 'Preserve compiled simulator'
    code, _ = run(['/usr/bin/gcc', '-fPIC', '-c', TASK / 'my_design/compat/pthread_yield_compat.c', '-o', case / 'compat.o'], 'compat', 120)
    assert code == 0
    code, _ = run(['/home/zhz/miniforge3/bin/python3', TASK / 'scripts/vcs_vm.py', '-full64', '-sverilog',
                  '-timescale=1ns/1ps', '-assert', 'svaext', '+define+VCS', '+define+PE_REFERENCE_MACRO',
                  '+define+AXI_RX_TIMING_CHECK', '+define+AXI_RAM_OUTPUT_DELAY_NS=1.0',
                  '-f', case / 'rtl.f', PROJECT / 'tb/sram_reference_model.v',
                  case / 'axi_ram_model.sv', case / 'tb_soc.sv', case / 'original_baseline_full_monitor.sv',
                  '-top', 'tb_soc', '-top', 'original_baseline_full_monitor',
                  '-Mdir=' + str(case / 'csrc'), '-o', executable, '-LDFLAGS', case / 'compat.o'], 'compile', 1800)
    assert code == 0, str(case / 'compile.log')
    verify(preparation['source_sha256'])
    save('compile-summary.json', {'status': 'ORIGINAL_FULL_CPU_PE_RTL_COMPILED_NOT_INFERENCE_ACCEPTED', 'simulator_kind': 'VCS2016_unmodified_original_RTL',
         'simulator_sha256': sha(executable), 'preparation_sha256': sha(case / 'preparation.json'), 'commands': records})
if args.stage == 'compile':
    raise SystemExit(0)
compiled = json.loads((case / 'compile-summary.json').read_text())
assert sha(case / 'preparation.json') == compiled['preparation_sha256']
assert sha(executable) == compiled['simulator_sha256']
phase = 'prefix-%d' % args.prefix_cycles if args.prefix_cycles else 'full'
code, log = run(['stdbuf', '-oL', '-eL', executable, '+MEM_HEX=' + str(firmware / 'data.hex'),
                 '+MAX_CYCLES=1000000000000', '+REPORT=' + str(case / (phase + '-report.txt')),
                 '+LOGITS=' + str(case / (phase + '-logits.bin')),
                 *('+' + key + '=' + value for key, value in preparation['parameters'].items()),
                 *(['+PREFIX_CYCLES=' + str(args.prefix_cycles)] if args.prefix_cycles else [])], phase, args.wall_limit_s)
verify(preparation['source_sha256'])
assert sha(executable) == compiled['simulator_sha256']
clean = code == 0 and not re.search(r'(?i)Fatal:|Error-\[|ORIGINAL_BASELINE_FAIL|AXI_RX_(?:SETUP_FAIL|UNKNOWN)', log)
if args.prefix_cycles:
    passed = clean and 'ORIGINAL_BASELINE_PREFIX_ONLY CPU_cycle=%d' % args.prefix_cycles in log
    status = 'ORIGINAL_CPU_PE_PREFIX_ONLY_NOT_INFERENCE_OR_PERFORMANCE_ACCEPTANCE' if passed else 'ORIGINAL_CPU_PE_PREFIX_DIAGNOSTIC_FAILURE'
else:
    passed = clean and 'ORIGINAL_BASELINE_FULL_MODEL_PASS nodes=1396 MAC_jobs=77' in log and 'ORIGINAL_BASELINE_CPU_RETURN_PASS' in log
    status = 'ORIGINAL_CPU_PE_FULL_RV32_SINGLE_IMAGE_FROZEN_C_PASS_OFFICIAL_ACCURACY_OPEN' if passed else 'ORIGINAL_CPU_PE_FULL_MODEL_DIAGNOSTIC_FAILURE'
result = {'status': status, 'image': args.image, 'full_model_accepted': passed and not args.prefix_cycles,
          'official_accuracy_accepted': False, 'before_after_ADP_accepted': False, 'commands': records,
          'source_sha256': preparation['source_sha256'], 'simulator_sha256': compiled['simulator_sha256'],
          'preparation_sha256': compiled['preparation_sha256'], 'report': (case / (phase + '-report.txt')).read_text(),
          'scope': 'Actual original CPU and PE RTL cycles, original unfused graph/C/MMIO four-plane PE. Prefix mode, if selected, is only a simulator execution check. Full mode checks all1396 nodes,77MACjobs, actual AXI counts, final1000 frozen-C logits using the existing internal C criterion. No organizer accuracy or final ADP conclusion.'}
logits = case / (phase + '-logits.bin')
if logits.exists():
    assert logits.stat().st_size == 4000
    expected = ROOT / 'work/fusion-proof' / (args.image + '.baseline')
    result.update(logits_sha256=sha(logits), logits_byte_exact=logits.read_bytes() == expected.read_bytes())
save(phase + ('-summary.json' if passed else '-failure.json'), result)
assert passed, str(case / (phase + '-failure.json'))
print(status, flush=True)
