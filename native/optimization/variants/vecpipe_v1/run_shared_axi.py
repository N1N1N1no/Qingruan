"""Run the qualified AXI suite through the actual complete shared backend.

This checks shared DIV ownership/drain and the full SRAM/AXI multiplexers in
addition to the already-qualified isolated engine, with the same test cases.
"""
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
TASK = BASE / 'native/audits/20261107/riscv_pe_contest/contest/task1'
HF = ROOT / 'vendor/HardFloat-1/source'
case = ROOT / 'work/vecpipe-shared-axi-v1'
assert not case.exists(), 'Preserve prior attempt'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def local(name):
    for prefix in ['/workspace/', '/mnt/data/Ubuntu/qingruan/']:
        if name.startswith(prefix):
            return BASE / name[len(prefix):]
    return Path(name)


oldcase = ROOT / 'work/vecpipe-axi-v1'
proof = json.loads((oldcase / 'summary.json').read_text())
assert proof['status'] == 'VECPIPE_AXI_REFERENCE_FAULT_CANCEL_RESET_THREE_SRAM_GEOMETRIES_PASS'
for name, expected in proof['source_sha256'].items():
    assert sha(local(name)) == expected, name
case.mkdir()
text = (oldcase / 'tb_pipeline.sv').read_text()
start = text.index('axi_vector_engine_vecpipe #')
end = text.index('));', text.index('affine_array', start)) + 3
fields = ['a', 'b', 'c', 'destination', 'elements', 'rows', 'a_stride', 'b_stride',
          'c_stride', 'destination_stride', 'operation', 'flags', 'zero', 'scalar_b', 'scalar_c', 'scalar_d']
axi_fields = ['awaddr','awid','awlen','awsize','awburst','awvalid','awready','wdata','wstrb','wlast','wvalid','wready',
              'bid','bresp','bvalid','bready','araddr','arid','arlen','arsize','arburst','arvalid','arready',
              'rdata','rid','rresp','rlast','rvalid','rready']
ports = [('clk','clk'), ('rst_n','rst_n'), ('request_valid','cmd_valid'), ('request_ready','cmd_ready'),
         ('request_opcode', "8'd7"), ('request_arguments','{' + ','.join('cmd_' + name for name in reversed(fields)) + '}'),
         ('cancel','cancel'), ('engine_busy','busy'), ('engine_done','done'), ('engine_error','error'),
         ('engine_cycles','cycles'), ('engine_bytes','streamed_bytes')]
ports += [('m_axi_' + name, 'm_axi_' + name) for name in axi_fields]
instance = 'axi_accel_backend_vecpipe #(.ROWS(BANKS/32),.COLS(32),.LANES(4))dut(\n ' + ',\n '.join('.%s(%s)' % item for item in ports) + '\n);'
text = text[:start] + instance + text[end:]
text = text.replace('dut.', 'dut.u_vector.').replace('dut.u_vector.PRIVATE.u_alu.', 'dut.u_shared_fp32.')
text = text.replace('initial begin\n if(!$value$plusargs', '''always@(posedge clk)if(rst_n&&dut.fp_valid&&dut.owner!=5&&dut.owner!=6&&dut.owner!=7)
 $fatal(1,"VECPIPE_SHARED_FAIL orphaned FP response");
initial begin
 if(!$value$plusargs''')
bench = case / 'tb_shared.sv'
bench.write_text(text)
files = list(dict.fromkeys([local(name) for name in json.loads((ROOT / 'hardfloat-files.json').read_text())] +
    [ROOT / 'rtl' / name for name in ['fp32_exp.sv','fp32_softmax_row.sv','fp32_layernorm.sv','fp32_vector.sv',
     'axi_softmax_engine.sv','axi_layernorm_engine.sv','axi_reorder_engine.sv','axi_dma_reader.sv','axi_dma_writer.sv',
     'axi_dma_copy.sv','axi_gelu_engine.sv','gelu_lut_stream.sv','axi_affine_gemm.sv','quant_lookup8.v','axi_quant_engine.sv','affine_array.v']] +
    [HERE / name for name in ['fp32_vector_pipeline.sv','axi_vector_engine_vecpipe.sv','axi_accel_backend_vecpipe.sv']] +
    [TASK / 'my_design/project/rtl/rfsp16x8.v', TASK / 'my_design/project/tb/sram_reference_model.v', bench]))
vectors = ROOT / 'work/vector-dma-reference/vectors.txt'
hashes = {str(path): sha(path) for path in [Path(__file__), oldcase / 'summary.json', oldcase / 'tb_pipeline.sv', vectors, *files]}
records = []
results = []


def run(argv, label, directory, limit):
    argv = list(map(str, argv))
    print('VECPIPE_SHARED_AXI_PHASE ' + directory.name + ' ' + label, flush=True)
    started = time.monotonic()
    def limits():
        resource.setrlimit(resource.RLIMIT_AS, (8 * 1024**3, 8 * 1024**3))
    with (directory / (label + '.log')).open('w') as log:
        process = subprocess.Popen(argv, cwd=directory, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True, preexec_fn=limits)
        try:
            while True:
                try:
                    code = process.wait(timeout=30)
                    break
                except subprocess.TimeoutExpired:
                    print('VECPIPE_SHARED_AXI_RUNNING ' + directory.name + ' ' + label, flush=True)
                    if time.monotonic() - started > limit:
                        raise TimeoutError(label)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=10)
    records.append({'label': label, 'argv': argv, 'returncode': code, 'elapsed_s': time.monotonic() - started})
    assert code == 0, str(directory / (label + '.log'))
    return (directory / (label + '.log')).read_text(errors='replace')


run(['/usr/bin/gcc','-fPIC','-c',TASK / 'my_design/compat/pthread_yield_compat.c','-o',case / 'compat.o'], 'compat', case, 120)
for banks in [128, 256, 512]:
    directory = case / str(banks)
    directory.mkdir()
    run(['/home/zhz/miniforge3/bin/python3',TASK / 'scripts/vcs_vm.py','-full64','-sverilog','-timescale=1ns/1ps',
         '+define+PE_REFERENCE_MACRO','+define+VECTOR_BANKS=' + str(banks), '+incdir+' + str(HF), '+incdir+' + str(HF / 'RISCV'),
         *files,'-top','tb_vecpipe_axi','-Mdir=' + str(directory / 'csrc'),'-o',directory / 'simv','-LDFLAGS',case / 'compat.o'], 'compile', directory, 600)
    simulator = sha(directory / 'simv')
    log = run([directory / 'simv','+VECTORS=' + str(vectors)], 'normal', directory, 1800)
    assert log.count('AXI_VECTOR_PASS banks=') == 1 and log.count('VECPIPE_AXI_EXTRA_PASS fast_cancel=2 reset=2') == 1
    assert not re.search(r'Fatal:|Error-\[|VECPIPE_(?:SHARED|AXI|FIFO)_FAIL|AXI_VECTOR_FAIL', log)
    cases = [dict(zip(['case','op','cycles','bytes','error'], map(int, match.groups())))
             for match in re.finditer(r'VECPIPE_AXI_CASE case=(\d+) op=(\d+) cycles=(\d+) bytes=(\d+) error=(\d+)', log)]
    isolated = next(item for item in proof['results'] if item['mode'] == 'pipeline' and item['banks'] == banks)
    assert cases == isolated['cases'], 'Shared backend changed accepted command cycles/results/errors'
    assert sha(directory / 'simv') == simulator
    results.append({'banks': banks, 'commands': len(cases), 'engine_cycles_byte_equal_to_isolated_pipeline': True,
                    'simulator_sha256': simulator, 'marker': re.search(r'AXI_VECTOR_PASS[^\n]+', log)[0]})
for name, expected in hashes.items():
    assert sha(Path(name)) == expected, name
summary = {'status': 'VECPIPE_COMPLETE_SHARED_BACKEND_AXI_FAULT_CANCEL_RESET_THREE_GEOMETRIES_PASS',
           'results': results, 'source_sha256': hashes, 'commands': records,
           'scope': 'Actual shared eight-engine backend and one physical SRAM array, vector requests with full numeric/AXI/fault/cancel/reset suite. All 220 engine-command counters per geometry match the isolated pipeline exactly, shared iterative DIV drains and no orphaned FP responses. Separate RV32 full inference and PPA gates still required.',
           'full_model_accepted': False, 'official_accuracy_accepted': False, 'final_ADP_accepted': False}
(case / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
print(summary['status'], flush=True)
