"""LayerNorm arithmetic/order/stream tests, including 1536-element rows."""
import hashlib,json,re,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parent;BASE=ROOT.parents[1]
TASK=BASE/'native/audits/20261107/riscv_pe_contest/contest/task1';HF=ROOT/'vendor/HardFloat-1/source'
case=ROOT/'work/layernorm-test';case.mkdir(parents=True,exist_ok=True)
def run(argv,label):
 print('LAYERNORM_PHASE='+label,flush=True)
 with(case/(label+'.log')).open('w')as log:r=subprocess.run(list(map(str,argv)),cwd=case,stdout=log,stderr=subprocess.STDOUT,timeout=1200)
 if r.returncode:raise RuntimeError(str(case/(label+'.log')))
 return(case/(label+'.log')).read_text(errors='replace')
run(['/home/zhz/miniforge3/envs/task2-cnn/bin/python','-B',ROOT/'prepare_layernorm_reference.py'],'oracle')
run(['/usr/bin/gcc','-fPIC','-c',TASK/'my_design/compat/pthread_yield_compat.c','-o',case/'compat.o'],'compat')
files=[Path(x)for x in json.loads((ROOT/'hardfloat-files.json').read_text())]+[ROOT/'rtl/fp32_layernorm.sv',ROOT/'tests/tb_fp32_layernorm.sv']
vectors=ROOT/'work/layernorm-reference/vectors.txt';hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest()for p in files+[vectors]}
run(['/home/zhz/miniforge3/bin/python3',TASK/'scripts/vcs_vm.py','-full64','-sverilog','-timescale=1ns/1ps','+incdir+'+str(HF),'+incdir+'+str(HF/'RISCV'),*files,
 '-top','tb_fp32_layernorm','-Mdir='+str(case/'csrc'),'-o',case/'simv','-LDFLAGS',case/'compat.o'],'compile')
log=run([case/'simv','+VECTORS='+str(vectors)],'simulate');marker=re.search(r'FP32_LAYERNORM_PASS[^\n]+',log);assert marker and'Fatal:'not in log
for p,want in hashes.items():assert hashlib.sha256(Path(p).read_bytes()).hexdigest()==want
summary={'status':'PASS','result':marker[0],'source_sha256':hashes,'reference':json.loads((ROOT/'work/layernorm-reference/summary.json').read_text()),
 'scope':'ordered two-pass float32 LayerNorm core with one shared external HardFloat ALU; finite bitwise/NaN class, 1536 elements, backpressure, cancellation drains and reset; DMA/RV32/synthesis/full inference pending'}
(case/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(marker[0],flush=True)
