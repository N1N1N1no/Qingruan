"""Exact ordered Softmax using the actual PE SRAM macros and one FP32 ALU."""
import hashlib,json,re,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parent;BASE=ROOT.parents[1]
TASK=BASE/'native/audits/20261107/riscv_pe_contest/contest/task1';HF=ROOT/'vendor/HardFloat-1/source'
case=ROOT/'work/softmax-row';case.mkdir(parents=True,exist_ok=True)
def run(argv,label):
 print('SOFTMAX_ROW_PHASE='+label,flush=True)
 with(case/(label+'.log')).open('w')as log:r=subprocess.run(list(map(str,argv)),cwd=case,stdout=log,stderr=subprocess.STDOUT,timeout=1200)
 if r.returncode:raise RuntimeError(str(case/(label+'.log')))
 return(case/(label+'.log')).read_text(errors='replace')
run(['/home/zhz/miniforge3/envs/task2-cnn/bin/python','-B',ROOT/'prepare_softmax_reference.py'],'oracle')
run(['/usr/bin/gcc','-fPIC','-c',TASK/'my_design/compat/pthread_yield_compat.c','-o',case/'compat.o'],'compat')
files=[Path(x)for x in json.loads((ROOT/'hardfloat-files.json').read_text())]+[ROOT/'rtl'/x for x in['fp32_exp.sv','fp32_softmax_row.sv','affine_array.v']]+[TASK/'my_design/project/rtl/rfsp16x8.v',TASK/'my_design/project/tb/sram_reference_model.v',ROOT/'tests/tb_fp32_softmax_row.sv']
hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest()for p in files};results=[]
for banks in[128,256]:
 run(['/home/zhz/miniforge3/bin/python3',TASK/'scripts/vcs_vm.py','-full64','-sverilog','-timescale=1ns/1ps',
  '+define+PE_REFERENCE_MACRO','+define+SOFTMAX_BANKS='+str(banks),'+incdir+'+str(HF),'+incdir+'+str(HF/'RISCV'),*files,
  '-top','tb_fp32_softmax_row','-Mdir='+str(case/('csrc_'+str(banks))),'-o',case/('simv_'+str(banks)),'-LDFLAGS',case/'compat.o'],'compile_'+str(banks))
 log=run([case/('simv_'+str(banks)),'+VECTORS='+str(ROOT/'work/softmax-reference/vectors.txt')],'simulate_'+str(banks))
 marker=re.search(r'FP32_SOFTMAX_ROW_PASS[^\n]+',log);assert marker and'Fatal:'not in log
 results.append({'banks':banks,'result':marker[0]})
for p,want in hashes.items():assert hashlib.sha256(Path(p).read_bytes()).hexdigest()==want
summary={'status':'PASS','results':results,'source_sha256':hashes,'reference':json.loads((ROOT/'work/softmax-reference/summary.json').read_text()),
 'scope':'row core with actual PE SRAM and shared HardFloat/Newlib-exp, bitwise finite outputs and NaN class; DMA/RV32 and whole-model integration pending'}
(case/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print('FP32_SOFTMAX_ROWS_VERIFIED',flush=True)
