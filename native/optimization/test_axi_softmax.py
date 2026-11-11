"""Real AXI, shared PE SRAM, ordered Softmax, faults and cancellation drains."""
import hashlib,json,re,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parent;BASE=ROOT.parents[1]
TASK=BASE/'native/audits/20261107/riscv_pe_contest/contest/task1';HF=ROOT/'vendor/HardFloat-1/source'
case=ROOT/'work/axi-softmax';case.mkdir(parents=True,exist_ok=True)
def run(argv,label):
 print('AXI_SOFTMAX_PHASE='+label,flush=True)
 with(case/(label+'.log')).open('w')as log:r=subprocess.run(list(map(str,argv)),cwd=case,stdout=log,stderr=subprocess.STDOUT,timeout=1200)
 if r.returncode:raise RuntimeError(str(case/(label+'.log')))
 return(case/(label+'.log')).read_text(errors='replace')
run(['/usr/bin/gcc','-fPIC','-c',TASK/'my_design/compat/pthread_yield_compat.c','-o',case/'compat.o'],'compat')
files=[Path(x)for x in json.loads((ROOT/'hardfloat-files.json').read_text())]+[ROOT/'rtl'/x for x in[
 'fp32_exp.sv','fp32_softmax_row.sv','axi_dma_reader.sv','axi_dma_writer.sv','axi_softmax_engine.sv','affine_array.v']]+[
 TASK/'my_design/project/rtl/rfsp16x8.v',TASK/'my_design/project/tb/sram_reference_model.v',ROOT/'tests/tb_axi_softmax_engine.sv']
vectors=ROOT/'work/softmax-reference/vectors.txt'
hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest()for p in files+[vectors]}
run(['/home/zhz/miniforge3/bin/python3',TASK/'scripts/vcs_vm.py','-full64','-sverilog','-timescale=1ns/1ps','+define+PE_REFERENCE_MACRO',
 '+incdir+'+str(HF),'+incdir+'+str(HF/'RISCV'),*files,'-top','tb_axi_softmax_engine','-Mdir='+str(case/'csrc'),'-o',case/'simv','-LDFLAGS',case/'compat.o'],'compile')
log=run([case/'simv','+VECTORS='+str(vectors)],'simulate')
marker=re.search(r'AXI_SOFTMAX_PASS[^\n]+',log);assert marker and'Fatal:'not in log
for p,want in hashes.items():assert hashlib.sha256(Path(p).read_bytes()).hexdigest()==want
summary={'status':'PASS','result':marker[0],'source_sha256':hashes,'additional_SRAM_macros':0,
 'scope':'shared PE SRAM, actual 64-bit AXI burst/4KiB boundaries, finite bitwise/NaN class, 64 alignment pairs, multirow/strides/broadcast, faults/cancel/drain/recovery; RV32 and whole-model integration pending'}
(case/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(marker[0],flush=True)
