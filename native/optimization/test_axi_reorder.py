"""All 62 physical model transposes through actual multidimensional AXI DMA."""
import hashlib,json,re,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent;BASE=ROOT.parents[1]
TASK=BASE/'native/audits/20261107/riscv_pe_contest/contest/task1'
case=ROOT/'work/axi-reorder';case.mkdir(parents=True,exist_ok=True)
def run(argv,label):
 print('AXI_REORDER_PHASE='+label,flush=True);begin=time.monotonic()
 with(case/(label+'.log')).open('w')as log:
  p=subprocess.Popen(list(map(str,argv)),cwd=case,stdout=log,stderr=subprocess.STDOUT)
  while True:
   try:code=p.wait(timeout=30);break
   except subprocess.TimeoutExpired:
    print('AXI_REORDER_RUNNING '+label,flush=True)
    if time.monotonic()-begin>1800:p.terminate();p.wait(timeout=20);raise TimeoutError(label)
 if code:raise RuntimeError(str(case/(label+'.log')))
 return(case/(label+'.log')).read_text(errors='replace')
run(['/home/zhz/miniforge3/envs/task2-cnn/bin/python','-B',ROOT/'prepare_reorder_reference.py'],'descriptors')
run(['/usr/bin/gcc','-fPIC','-c',TASK/'my_design/compat/pthread_yield_compat.c','-o',case/'compat.o'],'compat')
files=[ROOT/'rtl'/x for x in['axi_dma_reader.sv','axi_dma_writer.sv','axi_reorder_engine.sv']]+[ROOT/'tests/tb_axi_reorder_engine.sv']
vectors=ROOT/'work/reorder-reference/vectors.txt';hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest()for p in files+[vectors]}
run(['/home/zhz/miniforge3/bin/python3',TASK/'scripts/vcs_vm.py','-full64','-sverilog','-timescale=1ns/1ps',*files,
 '-top','tb_axi_reorder_engine','-Mdir='+str(case/'csrc'),'-o',case/'simv','-LDFLAGS',case/'compat.o'],'compile')
log=run([case/'simv','+VECTORS='+str(vectors)],'simulate');marker=re.search(r'AXI_REORDER_PASS[^\n]+',log);assert marker and'Fatal:'not in log
for p,want in hashes.items():assert hashlib.sha256(Path(p).read_bytes()).hexdigest()==want
summary={'status':'PASS','result':marker[0],'source_sha256':hashes,'descriptor_proof':json.loads((ROOT/'work/reorder-reference/summary.json').read_text()),
 'additional_SRAM_macros':0,'scope':'all62 active model transpose descriptions, every byte through actual AXI DMA, 64 alignment pairs, four-dimensional carries/broadcast, boundaries, fault/cancel/drain/recovery; module test, no full inference/RV32/synthesis claim'}
(case/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(marker[0],flush=True)
