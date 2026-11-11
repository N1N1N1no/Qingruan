"""Full-graph functional RTL dataflow proof, distinct from full RV32 timing.

Every MAC, fused GELU and remaining quantizer runs in the same RTL backend.
Remaining graph nodes and Conv packing execute on the host. The measured
engine cycles exclude that host work and cannot be used as end-to-end ADP.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parent
BASE=ROOT.parents[1]
SWIN=BASE/'native/swin-tiny'
TASK=BASE/'native/audits/20261107/riscv_pe_contest/contest/task1'
parser=argparse.ArgumentParser()
parser.add_argument('--image',default='ILSVRC2012_val_00000274.JPEG')
parser.add_argument('--compile-only',action='store_true')
parser.add_argument('--reuse-compile',action='store_true')
parser.add_argument('--case-name',default='rtl-model-quant')
parser.add_argument('--rows',type=int,default=8);parser.add_argument('--cols',type=int,default=32);parser.add_argument('--lanes',type=int,default=4)
parser.add_argument('--checkpoint-name',default='checkpoint')
parser.add_argument('--checkpoint-interval',type=int,default=5000000)
parser.add_argument('--stop-after-checkpoint',action='store_true')
options=parser.parse_args()
assert re.fullmatch(r'ILSVRC2012_val_\d{8}\.JPEG',options.image)
assert re.fullmatch(r'[a-zA-Z0-9_-]+',options.case_name)
assert re.fullmatch(r'[a-zA-Z0-9_-]+',options.checkpoint_name) and options.checkpoint_interval>=0
geometry={'rows':options.rows,'columns':options.cols,'lanes':options.lanes}
case=ROOT/'work'/options.case_name
case.mkdir(parents=True,exist_ok=True)
assets=case/'assets';assets.mkdir(exist_ok=True)
results=case/options.image;results.mkdir(exist_ok=True)
commands=[]
def run(argv,label,timeout=1800):
 print('RTL_MODEL_PHASE='+label,flush=True)
 started=time.monotonic()
 with (case/(label+'.log')).open('w') as log:
  p=subprocess.Popen(list(map(str,argv)),cwd=case,stdout=log,stderr=subprocess.STDOUT)
  while True:
   try:code=p.wait(timeout=30);break
   except subprocess.TimeoutExpired:
    print('RTL_MODEL_RUNNING '+label,flush=True)
    if time.monotonic()-started>timeout:p.terminate();p.wait(timeout=20);raise TimeoutError(label)
 commands.append({'label':label,'argv':list(map(str,argv)),'returncode':code,'elapsed_s':time.monotonic()-started})
 if code:raise RuntimeError(str(case/(label+'.log')))

rtl_files=[ROOT/'rtl'/x for x in ['axi_dma_reader.sv','axi_dma_writer.sv','axi_dma_copy.sv','axi_gelu_engine.sv',
 'gelu_lut_stream.sv','axi_affine_gemm.sv','quant_lookup8.v','axi_quant_engine.sv','axi_accel_backend.sv','affine_array.v']]
inputs=list(rtl_files)
inputs += [TASK/'my_design/project/rtl/rfsp16x8.v',TASK/'my_design/project/tb/sram_reference_model.v',ROOT/'tests/tb_rtl_model.sv',
 ROOT/'c/rtl_mac_hook.inc',ROOT/'c/rtl_model_host.c',ROOT/'c/rtl_model_checkpoint.h',SWIN/'c/graph.c',SWIN/'c/pe_driver.c',SWIN/'c/graph.h',SWIN/'c/pe_driver.h',ROOT/'work/lookup-tables/quant-thresholds.json']
hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
if options.reuse_compile:
 manifest=json.loads((case/'compile-manifest.json').read_text())
 assert manifest.get('geometry')==geometry
 assert manifest['inputs_sha256']==hashes,'sources changed: recompile required'
 assert manifest['simulator_sha256']==hashlib.sha256((case/'simv').read_bytes()).hexdigest()
else:
 assert not (case/'compile-manifest.json').exists(),'existing compiled revision: reuse it or choose a fresh --case-name'
 snapshot=case/'source_snapshot';snapshot.mkdir(exist_ok=True)
 frozen_sources={}
 for index,p in enumerate(inputs):
  dst=snapshot/('%02d_'%index+p.name);shutil.copyfile(p,dst);frozen_sources[str(p)]=str(dst)
 for name in ['graph.bin','constants.bin','gelu-fusions.bin']:
  shutil.copyfile(ROOT/'work/fused-plan'/name,assets/name)
 shutil.copyfile(ROOT/'work/lookup-tables/gelu.bin',assets/'gelu.bin')
 shutil.copyfile(ROOT/'work/lookup-tables/quant-thresholds.bin',assets/'quant-thresholds.bin')
 offsets=[0xffffffff]*1396
 for q in json.loads((ROOT/'work/lookup-tables/quant-thresholds.json').read_text()):offsets[q['node']]=q['threshold_offset']
 (case/'quant_layout.h').write_text('static const uint32_t quant_offset_by_node[1396]={'+','.join(hex(v)+'u' for v in offsets)+'};\n')
 original=(SWIN/'c/graph.c').read_text()
 marker='static int mac(SwinGraph *g,const SwinNode *n) {'
 call='if(!g->bus)status=host_mac(&c);else'
 assert original.count(marker)==original.count(call)==1
 adapted=original.replace(marker,(ROOT/'c/rtl_mac_hook.inc').read_text()+'\n'+marker).replace(call,'if(!g->bus)status=rtl_mac(&c);else')
 (case/'graph_rtl.c').write_text(adapted)
 vcs_home=Path(os.environ.get('VCS_HOME','/home/zhz/eda/synopsys/vcs-mx/L-2016.06'))
 assert (vcs_home/'include/svdpi.h').is_file()
 objects=[]
 for i,source in enumerate([case/'graph_rtl.c',SWIN/'c/pe_driver.c',ROOT/'c/rtl_model_host.c']):
  obj=case/('host_%d.o'%i);objects.append(obj)
  run(['/usr/bin/gcc','-O3','-march=native','-ffp-contract=off','-DSWIN_HOST','-fPIC','-I'+str(SWIN/'c'),'-I'+str(case),'-I'+str(vcs_home/'include'),'-c',source,'-o',obj],'host_compile_%d'%i)
 run(['/usr/bin/gcc','-fPIC','-c',TASK/'my_design/compat/pthread_yield_compat.c','-o',case/'compat.o'],'compat')
 rtl=rtl_files+[TASK/'my_design/project/rtl/rfsp16x8.v',TASK/'my_design/project/tb/sram_reference_model.v']
 run(['/home/zhz/miniforge3/bin/python3',TASK/'scripts/vcs_vm.py','-full64','-sverilog','-timescale=1ns/1ps',
  '+define+PE_REFERENCE_MACRO','+define+MODEL_ROWS='+str(options.rows),'+define+MODEL_COLS='+str(options.cols),'+define+MODEL_LANES='+str(options.lanes),*rtl,ROOT/'tests/tb_rtl_model.sv','-top','tb_rtl_model',
  '-Mdir='+str(case/'csrc'),'-o',case/'simv','-LDFLAGS',' '.join(map(str,[*objects,case/'compat.o']))+' -lm'],'compile')
 for p,want in hashes.items():assert hashlib.sha256(Path(p).read_bytes()).hexdigest()==want,'source changed during compile: '+p
 manifest={'inputs_sha256':hashes,'source_snapshots':frozen_sources,'geometry':geometry,'simulator_sha256':hashlib.sha256((case/'simv').read_bytes()).hexdigest(),
  'assets_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in assets.iterdir() if p.is_file()},'commands':commands}
 (case/'compile-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
if options.compile_only:raise SystemExit(0)
for name,want in manifest['assets_sha256'].items():assert hashlib.sha256((assets/name).read_bytes()).hexdigest()==want
source=ROOT/'work/fusion-proof'
inp=source/(options.image+'.input');expected=source/(options.image+'.baseline')
assert inp.is_file() and expected.is_file()
checkpoint=results/(options.checkpoint_name+'.bin')
identity=hashlib.sha256(json.dumps({'simulator':manifest['simulator_sha256'],'assets':manifest['assets_sha256'],'input':hashlib.sha256(inp.read_bytes()).hexdigest(),'expected':hashlib.sha256(expected.read_bytes()).hexdigest(),'geometry':geometry},sort_keys=True).encode()).hexdigest()
label='simulate_'+options.image+'_'+options.checkpoint_name+'_'+str(time.time_ns())
extra=['+STOP_AFTER_CHECKPOINT'] if options.stop_after_checkpoint else []
run([case/'simv','+PLAN='+str(assets),'+INPUT='+str(inp),'+EXPECTED='+str(expected),'+OUTPUT='+str(results/'logits.bin'),
 '+REPORT='+str(results/'summary.json'),'+CHECKPOINT='+str(checkpoint),'+IDENTITY='+identity,'+CHECKPOINT_INTERVAL='+str(options.checkpoint_interval),*extra],label,14400)
log=(case/(label+'.log')).read_text()
if options.stop_after_checkpoint:
 assert 'RTL_MODEL_CHECKPOINT_STOP' in log and checkpoint.is_file() and 'Fatal:' not in log
 print('RTL_MODEL_CHECKPOINT_READY '+str(checkpoint),flush=True);raise SystemExit(0)
assert 'RTL_FULL_GRAPH_FUNCTIONAL_PASS MAC_jobs=77 GELU=12 quantizers=166' in log and 'RTL_MODEL_AXI_PASS' in log and 'Fatal:' not in log
assert (results/'logits.bin').read_bytes()==expected.read_bytes()
summary=json.loads((results/'summary.json').read_text())
summary.update(scope='full graph with every MAC/GELU/quantizer in shared real RTL; remaining kernels/packing on host; no complete RV32 latency or ADP assertion',geometry=geometry,
 image=options.image,clock_period_ns=10,inputs_sha256=hashes,simulator_sha256=manifest['simulator_sha256'],
 logits_sha256=hashlib.sha256((results/'logits.bin').read_bytes()).hexdigest(),commands=commands)
summary.update(checkpoint_path=str(checkpoint),checkpoint_resumed='RTL_MODEL_CHECKPOINT_RESUMED' in log,checkpoint_identity=identity)
(results/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
print('RTL_MODEL_IMAGE_PASS '+options.image,flush=True)
