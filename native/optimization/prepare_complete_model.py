"""Build-only integration of all nonMAC engines into the RV32 model dispatcher.

No image, inference run, accuracy acceptance, or performance claim is made.
The frozen graph/compiler/model remain unchanged; adapted copies live in work/.
"""
import argparse,hashlib,json,re,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parent;BASE=ROOT.parents[1];SWIN=BASE/'native/swin-tiny'
PROJECT=BASE/'native/audits/20261107/riscv_pe_contest/contest/task1/my_design/project'
parser=argparse.ArgumentParser();parser.add_argument('--rows',type=int,default=8);parser.add_argument('--cols',type=int,default=32);parser.add_argument('--lanes',type=int,default=4);args=parser.parse_args()
assert args.rows>=4 and args.cols>=8 and args.rows*args.cols>=128
assert (args.rows,args.cols,args.lanes)in[(x['rows'],x['columns'],x['lanes'])for x in json.loads((ROOT/'work/array-functional/summary.json').read_text())]
case=ROOT/'work/complete-model-direct-build'/('R%d_C%d_L%d'%(args.rows,args.cols,args.lanes));case.mkdir(parents=True,exist_ok=True)
layout=ROOT/'work/accel-model/model_layout.h';assert layout.is_file(),'prepare original model layout first'
(case/'model_layout.h').write_bytes(layout.read_bytes())
hook='''#include "mac_plan.h"
#include "mac_dispatch.h"
extern volatile uint32_t model_mac_commands;
static int rtl_mac_complete(Mac *c){
 SwinGraph *g=c->g;const SwinJob *j=c->job;
 AccelMacDispatch md={.dispatch=&model_dispatch,.k_rows=MODEL_ARRAY_ROWS*16};
 uint32_t index=(uint32_t)(c->node-g->n);
 for(uint32_t batch=0;batch<(uint32_t)j->batches;++batch){
  uint32_t cc,dd;int status=accel_plan_mac(g,index,batch,0x83000000u,accel_emit_mac,&md,&cc,&dd);
  if(status)return status;++model_mac_commands;
  status=accel_plan_post(g,index,batch,cc,dd,accel_emit_planned,&model_dispatch);
  if(status)return status;
 }
 return 0;
}'''
original=(SWIN/'c/graph.c').read_text();start=original.index('static int mac(SwinGraph *g,const SwinNode *n) {');end=original.index('\nint swin_graph_node',start)
mac='''static int mac(SwinGraph *g,const SwinNode *n) {
 if(n->job<0||(uint32_t)n->job>=g->h->jobs)return -11;
 Mac c={g,n,g->j+n->job};int status=rtl_mac_complete(&c);
 if(status)return status;++g->executed_jobs;return 0;
}'''
adapted=original[:start]+'#include "nonmac_dispatch.h"\nextern AccelDispatch model_dispatch;\n'+hook+'\n'+mac+original[end:]
(case/'graph_accel.c').write_text(adapted)
main=(ROOT/'c/accel_model_main.c').read_text().replace('#include "accel_io.h"','#include "accel_io.h"\n#include "nonmac_dispatch.h"\nAccelDispatch model_dispatch={.timeout=1000000000ull};')
old='  }else{status=swin_graph_node(&g,i);++g.executed_nodes;}'
new='''  }else{
   status=accel_plan_node(&g,i,accel_emit_planned,&model_dispatch);
   if(status==1)status=swin_graph_node(&g,i);
   ++g.executed_nodes;
  }'''
assert main.count(old)==1;main=main.replace(old,new)
(case/'main.c').write_text(main)
compiler=Path('/home/zhz/rtl_tools/xpack-riscv-none-elf-gcc-14.2.0-3/bin/riscv-none-elf-gcc')
if not compiler.is_file():compiler=BASE/'native/rootfs/home/zhz/rtl_tools/xpack-riscv-none-elf-gcc-14.2.0-3/bin/riscv-none-elf-gcc'
prefix=str(compiler)[:-3]
sources=[PROJECT/'sw/start.S',case/'graph_accel.c',case/'main.c']+[ROOT/'c'/n for n in['accel_io.c','nonmac_plan.c','aux_plan.c','nonmac_dispatch.c','mac_plan.c','mac_dispatch.c']]
argv=[prefix+'gcc','-march=rv32imc_zicsr','-mabi=ilp32','-O2','-ffp-contract=off','-ffreestanding','-fno-builtin','-msmall-data-limit=0','-nostartfiles','-Wl,--no-relax','-Wl,-T,'+str(PROJECT/'sw/link.ld'),'-Wl,-Map,'+str(case/'program.map'),
 '-DMODEL_ARRAY_ROWS='+str(args.rows),'-I'+str(SWIN/'c'),'-I'+str(ROOT/'c'),'-I'+str(case),*map(str,sources),'-lm','-lc','-lgcc','-o',str(case/'program.elf')]
with(case/'compile.log').open('w')as f:subprocess.run(argv,stdout=f,stderr=subprocess.STDOUT,check=True)
subprocess.run([prefix+'objcopy','-O','binary',case/'program.elf',case/'program.bin'],check=True)
binary=(case/'program.bin').read_bytes();assert len(binary)<262144
(case/'program.hex').write_text(''.join('%02x\n'%v for v in binary))
inputs=sources+[Path(__file__),ROOT/'c/accel_model_main.c',ROOT/'c/aux_plan.h',ROOT/'c/mac_plan.h',ROOT/'c/mac_dispatch.h',ROOT/'c/nonmac_plan.h',ROOT/'c/nonmac_dispatch.h',SWIN/'c/graph.c',layout]
manifest={'status':'ALL8_RV32_FIRMWARE_BUILD_PASS_EXECUTION_PENDING','full_RV32_executed':False,'firmware_bytes':len(binary),'required_MMIO_supported_mask':'0x1fe','compiler_argv':argv,
 'array_geometry':{'rows':args.rows,'columns':args.cols,'lanes':args.lanes,'GEMM_K_rows':args.rows*16},'source_sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest()for p in inputs},'firmware_sha256':hashlib.sha256((case/'program.elf').read_bytes()).hexdigest(),
 'scope':'build-only isolated model dispatcher; raw MAC input tensor addresses, DMA Conv im2col, no CPU element packing or intermediate INT32 copy; hardware postscale and separate Conv FP scratch/NCHW reorder; no inference execution, accuracy or timing acceptance; remaining software kernels retained'}
(case/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n');print(json.dumps(manifest,indent=2),flush=True)
