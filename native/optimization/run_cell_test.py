import json,os,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parent;BASE=ROOT.parents[1]
TASK=BASE/'native/audits/20261107/riscv_pe_contest/contest/task1';PROJECT=TASK/'my_design/project'
case=ROOT/'work/cell-functional';case.mkdir(parents=True,exist_ok=True)
def run(args,name):
    print('CELL_TEST_PHASE='+name,flush=True)
    with (case/(name+'.log')).open('w') as log:r=subprocess.run(list(map(str,args)),cwd=case,stdout=log,stderr=subprocess.STDOUT,timeout=900)
    if r.returncode:raise RuntimeError(name+' failed')
run(['/usr/bin/gcc','-fPIC','-c',TASK/'my_design/compat/pthread_yield_compat.c','-o',case/'compat.o'],'compat')
run(['/home/zhz/miniforge3/bin/python3',TASK/'scripts/vcs_vm.py','-full64','-sverilog','-timescale=1ns/1ps','+define+PE_REFERENCE_MACRO',
     PROJECT/'rtl/rfsp16x8.v',ROOT/'rtl/affine_pe_cell.v',PROJECT/'tb/sram_reference_model.v',ROOT/'tests/tb_affine_cell.sv',
     '-top','tb_affine_cell','-Mdir='+str(case/'csrc'),'-o',case/'simv','-LDFLAGS',case/'compat.o'],'compile')
run([case/'simv'],'simulate')
text=(case/'simulate.log').read_text();assert 'AFFINE_CELL_PASS checked=2088968 lanes=8' in text and 'Fatal:' not in text,text[-2000:]
(case/'summary.json').write_text(json.dumps({'status':'PASS','lanes':8,'checked_products_and_psums':2088968,
                                         'centered_A_range':[-255,255],'centered_B_range':[-255,255],
                                         'scope':'candidate PE cell exhaustive arithmetic and raw SRAM/zero-point path; not full SoC'},indent=2)+'\n')
print('AFFINE_CELL_FUNCTIONAL_PASS',flush=True)
