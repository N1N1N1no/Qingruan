"""All real model GELU lookup values through the original PE SRAM macros."""
import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parents[1]
TASK = BASE / 'native/audits/20261107/riscv_pe_contest/contest/task1'
case = ROOT / 'work/gelu-stream'
case.mkdir(parents=True, exist_ok=True)
tables = (ROOT / 'work/lookup-tables/gelu.bin').read_bytes()
assert len(tables) == 4521984
(case / 'gelu.hex').write_text(''.join('%02x\n' % b for b in tables))
commands = []
def run(argv, label):
    print('GELU_STREAM_PHASE=' + label, flush=True)
    with (case / (label + '.log')).open('w') as log:
        r = subprocess.run(list(map(str, argv)), cwd=case, stdout=log, stderr=subprocess.STDOUT, timeout=1200)
    commands.append({'phase': label, 'returncode': r.returncode})
    if r.returncode:
        raise RuntimeError(str(case / (label + '.log')))

run(['/usr/bin/gcc', '-fPIC', '-c', TASK / 'my_design/compat/pthread_yield_compat.c', '-o', case / 'compat.o'], 'compat')
files = [ROOT / 'rtl/gelu_lut_stream.sv', ROOT / 'rtl/affine_array.v',
    TASK / 'my_design/project/rtl/rfsp16x8.v',
    TASK / 'my_design/project/tb/sram_reference_model.v', ROOT / 'tests/tb_gelu_lut_stream.sv']
run(['/home/zhz/miniforge3/bin/python3', TASK / 'scripts/vcs_vm.py', '-full64', '-sverilog',
    '-timescale=1ns/1ps', '+define+PE_REFERENCE_MACRO', *files, '-top', 'tb_gelu_lut_stream',
    '-Mdir=' + str(case / 'csrc'), '-o', case / 'simv', '-LDFLAGS', case / 'compat.o'], 'compile')
run([case / 'simv', '+TABLES=' + str(case / 'gelu.hex')], 'simulate')
log = (case / 'simulate.log').read_text()
marker = re.search(r'GELU_LUT_STREAM_PASS[^\n]+', log)
assert marker and 'Fatal:' not in log
summary = {'status': 'PASS', 'result': marker[0], 'values': len(tables), 'table_sha256': hashlib.sha256(tables).hexdigest(),
    'additional_SRAM_macros': 0, 'commands': commands,
    'scope': 'all real GELU table values through shared 256 PE SRAM banks, elastic stream/backpressure; DMA and SoC integration pending'}
(case / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
print(marker[0], flush=True)
