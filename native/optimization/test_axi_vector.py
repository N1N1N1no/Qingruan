"""Vector row/scalar broadcast DMA through existing PE SRAM, all67 model DQs."""
import hashlib,json,re,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent;BASE=ROOT.parents[1]
TASK=BASE/'native/audits/20261107/riscv_pe_contest/contest/task1';HF=ROOT/'vendor/HardFloat-1/source'
case=ROOT/'work/axi-vector';case.mkdir(parents=True,exist_ok=True)
def run(argv,label):
 print('AXI_VECTOR_PHASE='+label,flush=True);begin=time.monotonic()
 with(case/(label+'.log')).open('w')as log:
  p=subprocess.Popen(list(map(str,argv)),cwd=case,stdout=log,stderr=subprocess.STDOUT)
  while True:
   try:code=p.wait(timeout=30);break
   except subprocess.TimeoutExpired:
    print('AXI_VECTOR_RUNNING '+label,flush=True)
    if time.monotonic()-begin>1800:p.terminate();p.wait(timeout=20);raise TimeoutError(label)
 if code:raise RuntimeError(str(case/(label+'.log')))
 return(case/(label+'.log')).read_text(errors='replace')
run(['/home/zhz/miniforge3/envs/task2-cnn/bin/python','-B',ROOT/'prepare_vector_dma_reference.py'],'oracle')
run(['/usr/bin/gcc','-fPIC','-c',TASK/'my_design/compat/pthread_yield_compat.c','-o',case/'compat.o'],'compat')
files=[Path(x)for x in json.loads((ROOT/'hardfloat-files.json').read_text())]+[ROOT/'rtl'/x for x in[
 'fp32_vector.sv','axi_dma_reader.sv','axi_dma_writer.sv','axi_vector_engine.sv','affine_array.v']]+[
 TASK/'my_design/project/rtl/rfsp16x8.v',TASK/'my_design/project/tb/sram_reference_model.v',ROOT/'tests/tb_axi_vector_engine.sv']
vectors=ROOT/'work/vector-dma-reference/vectors.txt';hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest()for p in files+[vectors]};results=[]
for banks in[128,256,512]:
 run(['/home/zhz/miniforge3/bin/python3',TASK/'scripts/vcs_vm.py','-full64','-sverilog','-timescale=1ns/1ps','+define+PE_REFERENCE_MACRO','+define+VECTOR_BANKS='+str(banks),
  '+incdir+'+str(HF),'+incdir+'+str(HF/'RISCV'),*files,'-top','tb_axi_vector_engine','-Mdir='+str(case/('csrc_'+str(banks))),'-o',case/('simv_'+str(banks)),'-LDFLAGS',case/'compat.o'],'compile_'+str(banks))
 log=run([case/('simv_'+str(banks)),'+VECTORS='+str(vectors)],'simulate_'+str(banks));marker=re.search(r'AXI_VECTOR_PASS[^\n]+',log);assert marker and'Fatal:'not in log
 results.append({'banks':banks,'result':marker[0]})
for p,want in hashes.items():assert hashlib.sha256(Path(p).read_bytes()).hexdigest()==want
summary={'status':'PASS','results':results,'source_sha256':hashes,'reference':json.loads((ROOT/'work/vector-dma-reference/summary.json').read_text()),'additional_SRAM_macros':0,
 'scope':'actual shared128/256/512 PE SRAM, arithmetic/DQ/postscale scalar/vector and row broadcast, actual67 model DQ parameter groups, all raw8 codes, S32 bias data, AXI alignment/fault/cancel/drain/recovery; RV32/broadcast plan lowering/synthesis pending'}
(case/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print('AXI_VECTOR_VERIFIED',flush=True)
