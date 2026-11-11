"""Actual RV32 driver invocation, original-order CPU GELU comparison, faults."""
import argparse
import hashlib
import json
import re
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parents[1]
TASK = BASE / 'native/audits/20261107/riscv_pe_contest/contest/task1'
PROJECT = TASK / 'my_design/project'
parser=argparse.ArgumentParser()
parser.add_argument('--unified',action='store_true',help='run MAC/GELU/MAC with the shared backend')
parser.add_argument('--converted-control',type=Path,help='also functionally check the CPU/control Verilog prepared for DC')
options=parser.parse_args()
unified=options.unified
assert not options.converted_control or unified
case = ROOT / ('work/accel-soc-converted' if options.converted_control else 'work/accel-soc' if unified else 'work/gelu-soc')
if (case/'summary.json').is_file():
 (case/'summary.json').rename(case/('summary.previous.%d.json'%time.time_ns()))
prefix_label='ACCEL_SOC' if unified else 'GELU_SOC'
top='soc_accel_top' if unified else 'soc_gelu_top'
firmware=ROOT / ('c/accel_soc_main.c' if unified else 'c/gelu_soc_main.c')
subprocess.run([sys.executable, str(ROOT / ('prepare_accel_soc.py' if unified else 'prepare_gelu_soc.py'))], check=True)
if options.converted_control:
 assert options.converted_control.is_file()
 case.mkdir(parents=True,exist_ok=True)
 for name in ['tb_soc.sv','axi_ram_model.sv']:shutil.copyfile(ROOT/'work/accel-soc'/name,case/name)
 shutil.copyfile(options.converted_control,case/(top+'.sv'))
source = BASE / 'native/swin-tiny/work/verify.ylnpqxth'
plan = json.loads((source / 'plan.json').read_text())
constants = (source / 'constants.bin').read_bytes()
f = json.loads((ROOT / 'work/lookup-tables/gelu-fusions.json').read_text())[0]
def floats(tensor, count):
    offset = plan['tensors'][tensor]['offset']
    return struct.unpack_from('<%df' % count, constants, offset)
defines = {'INPUT_SCALE': f['input_scale'], 'OUTPUT_SCALE': f['output_scale'],
    'SQRT_TWO': floats(661, 1)[0], 'ONE': floats(662, 1)[0], 'HALF': floats(663, 1)[0]}
header = '\n'.join('#define GELU_%s %sf' % (k, float(v).hex()) for k, v in defines.items()) + '\n'
header += '#define GELU_INPUT_ZERO %d\n#define GELU_OUTPUT_ZERO %d\n' % (f['input_zero'], f['output_zero'])
header += 'static const float gelu_bias[32]={' + ','.join(float(v).hex() + 'f' for v in floats(8, 32)) + '};\n'
table = (ROOT / 'work/lookup-tables/gelu.bin').read_bytes()[:8192]
header += 'static const uint8_t gelu_table[8192] __attribute__((aligned(64)))={' + ','.join(str(v) for v in table) + '};\n'
(case / 'gelu_sample.h').write_text(header)
if unified:
 q=json.loads((ROOT/'work/lookup-tables/quant-thresholds.json').read_text())[0]
 data=(ROOT/'work/lookup-tables/quant-thresholds.bin').read_bytes()[:1020]
 (case/'quant_sample.h').write_text('#define QUANT_SCALE '+float(q['scale']).hex()+'f\n#define QUANT_ZERO '+str(q['zero'])+'\nstatic const uint8_t quant_table[1020] __attribute__((aligned(64)))={'+','.join(str(v) for v in data)+'};\n')
commands = []
def run(argv, label, allow_failure=False):
    print(prefix_label+'_PHASE=' + label, flush=True)
    argv = list(map(str, argv)); start = time.monotonic()
    with (case / (label + '.log')).open('w') as log:
        r = subprocess.run(argv, cwd=case, stdout=log, stderr=subprocess.STDOUT, timeout=1800)
    commands.append({'argv': argv, 'phase': label, 'returncode': r.returncode, 'elapsed_s': time.monotonic() - start})
    if r.returncode and not allow_failure:
        raise RuntimeError(str(case / (label + '.log')))
    return (case / (label + '.log')).read_text(errors='replace')

prefix = '/home/zhz/rtl_tools/xpack-riscv-none-elf-gcc-14.2.0-3/bin/riscv-none-elf-'
run([prefix + 'gcc', '-march=rv32imc_zicsr', '-mabi=ilp32', '-O2', '-ffreestanding', '-fno-builtin', '-ffp-contract=off',
    '-msmall-data-limit=0', '-nostartfiles', '-Wl,--no-relax', '-Wl,-T,' + str(PROJECT / 'sw/link.ld'),
    '-I' + str(ROOT / 'c'), '-I' + str(case), PROJECT / 'sw/start.S', ROOT / 'c/accel_io.c', firmware,
    '-lm', '-lc', '-lgcc', '-o', case / 'program.elf'], 'rv32_compile')
run([prefix + 'objcopy', '-O', 'binary', case / 'program.elf', case / 'program.bin'], 'objcopy')
(case / 'program.hex').write_text('\n'.join('%02x' % v for v in (case / 'program.bin').read_bytes()) + '\n')
symbols = run([prefix + 'nm', '-S', case / 'program.elf'], 'symbols')
addresses = {}
symbol_sizes=[('gelu_kernel_cycles',32),('gelu_engine_results',24),('gelu_failure',4)]
if unified:symbol_sizes += [('mac_kernel_cycles',48),('mac_engine_results',36),('mac_failure',4),('quant_kernel_cycles',32),('quant_engine_results',24),('quant_failure',4)]
for name, size in symbol_sizes:
    m = re.search(r'^([0-9a-f]+)\s+([0-9a-f]+)\s+[Bb]\s+' + name + '$', symbols, re.M)
    assert m and int(m[2], 16) == size, name
    addresses[name] = m[1]
run(['/usr/bin/gcc', '-fPIC', '-c', TASK / 'my_design/compat/pthread_yield_compat.c', '-o', case / 'compat.o'], 'compat')
rtl = [ROOT / 'rtl' / x for x in ['axi_dma_reader.sv', 'axi_dma_writer.sv', 'axi_dma_arbiter.sv', 'axi_accel_mmio.sv',
    'gelu_lut_stream.sv', 'axi_gelu_engine.sv', 'affine_array.v']]
if unified:rtl += [ROOT/'rtl'/x for x in ['axi_dma_copy.sv','axi_affine_gemm.sv','quant_lookup8.v','axi_quant_engine.sv','axi_accel_backend.sv']]
extra_defines=['+define+UNIFIED_BACKEND'] if unified else []
run(['/home/zhz/miniforge3/bin/python3', TASK / 'scripts/vcs_vm.py', '-full64', '-sverilog', '-timescale=1ns/1ps', '-debug_access',
    '+define+VCS', '+define+PE_REFERENCE_MACRO', '+define+AXI_RX_TIMING_CHECK', '+define+AXI_RAM_OUTPUT_DELAY_NS=1.0',
    *extra_defines, '-assert', 'svaext', '-f', PROJECT / 'build/rtl.f', *rtl, case / (top+'.sv'),
    PROJECT / 'tb/sram_reference_model.v', case / 'axi_ram_model.sv', case / 'tb_soc.sv', ROOT / 'tests/gelu_soc_monitor.sv',
    '-top', 'tb_soc', '-top', 'gelu_soc_monitor', '-Mdir=' + str(case / 'csrc'), '-o', case / 'simv', '-LDFLAGS', case / 'compat.o'], 'compile')
digest = hashlib.sha256((case / 'simv').read_bytes()).hexdigest()
argv = [case / 'simv', '+MEM_HEX=' + str(case / 'program.hex'), '+CYCLES_ADDR=' + addresses['gelu_kernel_cycles'],
    '+RESULTS_ADDR=' + addresses['gelu_engine_results'], '+FAILURE_ADDR=' + addresses['gelu_failure'], '+MAX_CYCLES=10000000']
if unified:argv += ['+MAC_CYCLES_ADDR='+addresses['mac_kernel_cycles'],'+MAC_RESULTS_ADDR='+addresses['mac_engine_results'],'+MAC_FAILURE_ADDR='+addresses['mac_failure']]
if unified:argv += ['+QUANT_CYCLES_ADDR='+addresses['quant_kernel_cycles'],'+QUANT_RESULTS_ADDR='+addresses['quant_engine_results'],'+QUANT_FAILURE_ADDR='+addresses['quant_failure']]
normal = run([*argv, '+REPORT=' + str(case / 'normal-report.txt')], 'normal')
assert normal.count('GELU_SOC_MONITOR_PASS') == normal.count(prefix_label+'_CPU_RETURN_PASS') == 1 and not re.search(r'Fatal:|Error-\[|AXI_RX_(?:SETUP_FAIL|UNKNOWN)', normal), normal[-2000:]
negative = run([*argv, '+INJECT_GELU_ERROR', '+REPORT=' + str(case / 'negative-report.txt')], 'negative', True)
assert 'GELU_SOC_MONITOR_NEGATIVE_PASS' in negative and prefix_label+'_CPU_RETURN_FAIL code=bad00040' in negative and prefix_label+'_CPU_RETURN_PASS' not in negative, negative[-2000:]
assert hashlib.sha256((case / 'simv').read_bytes()).hexdigest() == digest
results,mac_results,quant_results = [],[],[]
for row in (case / 'normal-report.txt').read_text().splitlines():
    m = re.fullmatch(r'(QUANTCASE|MACCASE|CASE) (\d+) CPU=(\d+) DMA=(\d+) ENGINE=(\d+) BYTES=(\d+)', row)
    assert m, row
    kind=m[1];i, cpu, dma, eng, n = map(int, m.groups()[1:])
    destination=mac_results if kind=='MACCASE' else quant_results if kind=='QUANTCASE' else results
    cpu_name='CPU_integer_MAC_cycles' if kind=='MACCASE' else 'CPU_QuantizeLinear_cycles' if kind=='QUANTCASE' else 'CPU_original_order_GELU_cycles'
    destination.append({'case': i, cpu_name: cpu, 'RISC_V_accelerator_call_cycles': dma,
        'engine_cycles': eng, 'bytes': n, 'kernel_speedup': cpu / dma})
assert len(results) == 2
assert len(mac_results)==(3 if unified else 0)
assert len(quant_results)==(2 if unified else 0)
summary = {'status': 'PASS', 'scope': 'actual RV32 + DMA + shared PE SRAM GELU; 112 values vs CPU original-order float32 GELU, guards and corruption control; not full Swin',
    'full_model': False, 'sdf': False, 'legacy_PE_retained': False, 'MAC_frontend_connected': unified, 'SRAM_macros': 256,'generic_CPU_control_preparation_checked':bool(options.converted_control),
    'clock_period_ns': 10, 'RAM_response_delay_ns': 1, 'negative_control_detected': True, 'results': results,
    'simulator_sha256': digest, 'commands': commands, 'MAC_results':mac_results,'quantization_results':quant_results,
    'input_sha256': {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in [*rtl, ROOT / 'c/accel_io.c', firmware, ROOT / 'c/gelu_soc_main.c', ROOT / 'tests/gelu_soc_monitor.sv', case / 'gelu_sample.h', case / (top+'.sv')]}}
if unified:
 summary['scope']='actual RV32 + one shared 256-SRAM MAC/GELU backend plus exact quantizer; MAC/MAC/Q/GELU/GELU/Q/MAC checked against CPU with guards and corruption control; not full Swin'
 summary['input_sha256'][str(case/'quant_sample.h')]=hashlib.sha256((case/'quant_sample.h').read_bytes()).hexdigest()
(case / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
print(('ACCEL' if unified else 'GELU')+'_RV32_SOC_PASS', flush=True)
