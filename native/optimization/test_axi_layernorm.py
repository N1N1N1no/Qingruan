"""LayerNorm DMA with three interleaved planes in existing PE SRAM banks."""
import hashlib,json,re,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parent;BASE=ROOT.parents[1]
TASK=BASE/'native/audits/20261107/riscv_pe_contest/contest/task1';HF=ROOT/'vendor/HardFloat-1/source'
case=ROOT/'work/axi-layernorm';case.mkdir(parents=True,exist_ok=True)
def run(argv,label):
 print('AXI_LAYERNORM_PHASE='+label,flush=True)
 with(case/(label+'.log')).open('w')as log:r=subprocess.run(list(map(str,argv)),cwd=case,stdout=log,stderr=subprocess.STDOUT,timeout=1200)
 if r.returncode:raise RuntimeError(str(case/(label+'.log')))
 return(case/(label+'.log')).read_text(errors='replace')
run(['/usr/bin/gcc','-fPIC','-c',TASK/'my_design/compat/pthread_yield_compat.c','-o',case/'compat.o'],'compat')
files=[Path(x)for x in json.loads((ROOT/'hardfloat-files.json').read_text())]+[ROOT/'rtl'/x for x in[
 'fp32_layernorm.sv','axi_dma_reader.sv','axi_dma_writer.sv','axi_layernorm_engine.sv','affine_array.v']]+[
 TASK/'my_design/project/rtl/rfsp16x8.v',TASK/'my_design/project/tb/sram_reference_model.v',ROOT/'tests/tb_axi_layernorm_engine.sv']
vectors=ROOT/'work/layernorm-reference/vectors.txt';hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest()for p in files+[vectors]}
results=[]
for banks in[128,256]:
 run(['/home/zhz/miniforge3/bin/python3',TASK/'scripts/vcs_vm.py','-full64','-sverilog','-timescale=1ns/1ps','+define+PE_REFERENCE_MACRO','+define+LAYERNORM_BANKS='+str(banks),
  '+incdir+'+str(HF),'+incdir+'+str(HF/'RISCV'),*files,'-top','tb_axi_layernorm_engine','-Mdir='+str(case/('csrc_'+str(banks))),'-o',case/('simv_'+str(banks)),'-LDFLAGS',case/'compat.o'],'compile_'+str(banks))
 log=run([case/('simv_'+str(banks)),'+VECTORS='+str(vectors)],'simulate_'+str(banks))
 marker=re.search(r'AXI_LAYERNORM_PASS[^\n]+',log);assert marker and'Fatal:'not in log
 results.append({'banks':banks,'result':marker[0]})
for p,want in hashes.items():assert hashlib.sha256(Path(p).read_bytes()).hexdigest()==want
summary={'status':'PASS','results':results,'source_sha256':hashes,'additional_SRAM_macros':0,
 'scope':'two-pass ordered FP32 LayerNorm using actual 128/256 PE SRAM, rows through1536 floats with x/scale/bias interleaved blocks, AXI alignment/strides/broadcast/fault/cancel/drain/recovery; RV32/integrated backend/synthesis pending'}
(case/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print('AXI_LAYERNORM_VERIFIED',flush=True)
