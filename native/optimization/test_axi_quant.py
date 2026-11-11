"""All scalar model quantizers and AXI packing/abort/recovery integration."""
import hashlib,json,re,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parent;BASE=ROOT.parents[1]
TASK=BASE/'native/audits/20261107/riscv_pe_contest/contest/task1'
case=ROOT/'work/axi-quant';case.mkdir(parents=True,exist_ok=True)
vectors=ROOT/'work/quant-test'
def run(argv,label):
 print('AXI_QUANT_PHASE='+label,flush=True)
 with (case/(label+'.log')).open('w') as log:r=subprocess.run(list(map(str,argv)),cwd=case,stdout=log,stderr=subprocess.STDOUT,timeout=1200)
 if r.returncode:raise RuntimeError(str(case/(label+'.log')))
run(['/usr/bin/gcc','-fPIC','-c',TASK/'my_design/compat/pthread_yield_compat.c','-o',case/'compat.o'],'compat')
files=[ROOT/'rtl'/x for x in ['axi_dma_reader.sv','axi_dma_writer.sv','quant_lookup8.v','axi_quant_engine.sv']]+[ROOT/'tests/tb_axi_quant_engine.sv']
run(['/home/zhz/miniforge3/bin/python3',TASK/'scripts/vcs_vm.py','-full64','-sverilog','-timescale=1ns/1ps',*files,
 '-top','tb_axi_quant_engine','-Mdir='+str(case/'csrc'),'-o',case/'simv','-LDFLAGS',case/'compat.o'],'compile')
run([case/'simv','+TABLES='+str(vectors/'tables.hex'),'+VECTORS='+str(vectors/'vectors.txt')],'simulate')
log=(case/'simulate.log').read_text();marker=re.search(r'AXI_QUANT_PASS[^\n]+',log);assert marker and 'Fatal:' not in log
summary={'status':'PASS','result':marker[0],'scope':'all178 original scalar model quantizers with boundary +/-1ULP and random vectors through real AXI; alignments, tails, NaN rejection, faults/cancel/recovery; RV32/full graph integration pending',
 'inputs_sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files+[vectors/'tables.hex',vectors/'vectors.txt']}}
(case/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(marker[0],flush=True)
