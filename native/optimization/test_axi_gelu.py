"""Real model tables through DMA, shared SRAM lookup, and strided writeback."""
import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parents[1]
TASK = BASE / 'native/audits/20261107/riscv_pe_contest/contest/task1'
parser=argparse.ArgumentParser();parser.add_argument('--rows',type=int,default=8);parser.add_argument('--cols',type=int,default=32);parser.add_argument('--lanes',type=int,default=4);parser.add_argument('--sweep',action='store_true');opts=parser.parse_args()
case = ROOT / ('work/controller-sweep/R%d_C%d_L%d/gelu'%(opts.rows,opts.cols,opts.lanes) if opts.sweep else 'work/axi-gelu')
case.mkdir(parents=True, exist_ok=True)
tables = ROOT / 'work/gelu-stream/gelu.hex'
assert tables.is_file()
def run(argv, label):
    print('AXI_GELU_PHASE=' + label, flush=True)
    with (case / (label + '.log')).open('w') as log:
        r = subprocess.run(list(map(str, argv)), cwd=case, stdout=log, stderr=subprocess.STDOUT, timeout=1200)
    if r.returncode:
        raise RuntimeError(str(case / (label + '.log')))

run(['/usr/bin/gcc', '-fPIC', '-c', TASK / 'my_design/compat/pthread_yield_compat.c', '-o', case / 'compat.o'], 'compat')
files = [ROOT / 'rtl' / x for x in ['axi_dma_reader.sv', 'axi_dma_writer.sv', 'gelu_lut_stream.sv', 'axi_gelu_engine.sv', 'affine_array.v']]
files += [TASK / 'my_design/project/rtl/rfsp16x8.v', TASK / 'my_design/project/tb/sram_reference_model.v', ROOT / 'tests/tb_axi_gelu_engine.sv']
run(['/home/zhz/miniforge3/bin/python3', TASK / 'scripts/vcs_vm.py', '-full64', '-sverilog',
    '-timescale=1ns/1ps', '+define+PE_REFERENCE_MACRO','+define+ARRAY_ROWS='+str(opts.rows),'+define+ARRAY_COLS='+str(opts.cols),'+define+ARRAY_LANES='+str(opts.lanes), *files, '-top', 'tb_axi_gelu_engine',
    '-Mdir=' + str(case / 'csrc'), '-o', case / 'simv', '-LDFLAGS', case / 'compat.o'], 'compile')
run([case / 'simv', '+TABLES=' + str(tables)], 'simulate')
log = (case / 'simulate.log').read_text()
marker = re.search(r'AXI_GELU_PASS[^\n]+', log)
assert marker and 'Fatal:' not in log
summary = {'status': 'PASS', 'result': marker[0], 'full_model': False, 'additional_SRAM_macros': 0,
    'rows':opts.rows,'columns':opts.cols,'lanes':opts.lanes,
    'inputs_sha256': {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
    'scope': 'all12 real table sets loaded via AXI, 64 alignment pairs, row/channel blocks, backpressure, faults, cancellation/recovery; RV32 and whole-model integration pending'}
(case / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
print(marker[0], flush=True)
