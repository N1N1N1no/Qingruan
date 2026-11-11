"""Bitwise classic-Newlib exp controller, shared ALU and abort/drain tests."""
import hashlib,json,re,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent;BASE=ROOT.parents[1]
TASK=BASE/'native/audits/20261107/riscv_pe_contest/contest/task1';HF=ROOT/'vendor/HardFloat-1/source'
case=ROOT/'work/exp-test';case.mkdir(parents=True,exist_ok=True)
def run(argv,label):
 print('EXP_TEST_PHASE='+label,flush=True)
 with (case/(label+'.log')).open('w') as log:r=subprocess.run(list(map(str,argv)),cwd=case,stdout=log,stderr=subprocess.STDOUT,timeout=1200)
 if r.returncode:raise RuntimeError(str(case/(label+'.log')))
run(['/home/zhz/miniforge3/envs/task2-cnn/bin/python','-B',ROOT/'prepare_exp_reference.py'],'oracle')
oracle=json.loads((ROOT/'work/exp-reference/summary.json').read_text())
run(['/usr/bin/gcc','-fPIC','-c',TASK/'my_design/compat/pthread_yield_compat.c','-o',case/'compat.o'],'compat')
files=[Path(x)for x in json.loads((ROOT/'hardfloat-files.json').read_text())]+[ROOT/'rtl/fp32_exp.sv',ROOT/'tests/tb_fp32_exp.sv']
hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
run(['/home/zhz/miniforge3/bin/python3',TASK/'scripts/vcs_vm.py','-full64','-sverilog','-timescale=1ns/1ps',
 '+incdir+'+str(HF),'+incdir+'+str(HF/'RISCV'),*files,'-top','tb_fp32_exp','-Mdir='+str(case/'csrc'),'-o',case/'simv','-LDFLAGS',case/'compat.o'],'compile')
run([case/'simv','+VECTORS='+str(ROOT/'work/exp-reference/vectors.hex')],'simulate')
log=(case/'simulate.log').read_text();marker=re.search(r'FP32_EXP_PASS[^\n]+',log)
assert marker and 'vectors='+str(oracle['vectors']) in marker[0] and 'Fatal:' not in log
for p,want in hashes.items():assert hashlib.sha256(Path(p).read_bytes()).hexdigest()==want
summary={'status':'PASS','result':marker[0],'source_sha256':hashes,'reference':oracle,
 'scope':'classic Newlib float32 exp controller against archived C algorithm, all rounding points via shared HardFloat; installed RV32 numerical identity and vector/DMA integration remain separate'}
(case/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(marker[0],flush=True)
