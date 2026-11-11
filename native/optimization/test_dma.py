import argparse,json,re,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parent;BASE=ROOT.parents[1]
TASK=BASE/'native/audits/20261107/riscv_pe_contest/contest/task1'
parser=argparse.ArgumentParser();parser.add_argument('unit',choices=['reader','writer','copy','arbiter','mmio'],default='reader',nargs='?');args=parser.parse_args()
case=ROOT/'work'/('dma-'+args.unit);case.mkdir(parents=True,exist_ok=True)
def run(argv,name):
 print('DMA_'+args.unit.upper()+'_PHASE='+name,flush=True)
 with (case/(name+'.log')).open('w') as f:r=subprocess.run(list(map(str,argv)),cwd=case,stdout=f,stderr=subprocess.STDOUT,timeout=900)
 if r.returncode:raise RuntimeError(str(case/(name+'.log')))
run(['/usr/bin/gcc','-fPIC','-c',TASK/'my_design/compat/pthread_yield_compat.c','-o',case/'compat.o'],'compat')
files=[ROOT/'rtl'/('axi_dma_'+args.unit+'.sv')]
if args.unit=='copy':files=[ROOT/'rtl/axi_dma_reader.sv',ROOT/'rtl/axi_dma_writer.sv',*files]
if args.unit=='mmio':files=[ROOT/'rtl/axi_accel_mmio.sv']
run(['/home/zhz/miniforge3/bin/python3',TASK/'scripts/vcs_vm.py','-full64','-sverilog','-timescale=1ns/1ps',*files,ROOT/'tests'/('tb_dma_'+args.unit+'.sv'),'-top','tb_dma_'+args.unit,'-Mdir='+str(case/'csrc'),'-o',case/'simv','-LDFLAGS',case/'compat.o'],'compile')
run([case/'simv'],'simulate');text=(case/'simulate.log').read_text()
marker='DMA_'+args.unit.upper()+'_PASS';match=re.search(marker+r'[^\n]*',text)
assert match and 'Fatal:' not in text,text[-2000:]
(case/'summary.json').write_text(json.dumps({'status':'PASS','unit':args.unit,'result':match[0],
 'scope':'standalone AXI burst DMA unit, packing, striding, backpressure and faults; SoC/model integration pending'},indent=2)+'\n')
print(marker,flush=True)
