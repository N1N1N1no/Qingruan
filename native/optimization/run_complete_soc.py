"""Actual RV32 module integration: all8 engines, private vs pooled FP32.

This does not run an image/model or claim whole-SoC PPA/performance.
"""
import argparse,hashlib,json,re,shutil,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent;BASE=ROOT.parents[1]
TASK=BASE/'native/audits/20261107/riscv_pe_contest/contest/task1';PROJECT=TASK/'my_design/project';HF=ROOT/'vendor/HardFloat-1/source'
parser=argparse.ArgumentParser();parser.add_argument('--resume',action='store_true');parser.add_argument('--rows',type=int,default=8);parser.add_argument('--cols',type=int,default=32);parser.add_argument('--lanes',type=int,default=4);args=parser.parse_args()
geometry=(args.rows,args.cols,args.lanes);identifier='R%d_C%d_L%d'%geometry
assert geometry in[(x['rows'],x['columns'],x['lanes'])for x in json.loads((ROOT/'work/array-functional/summary.json').read_text())]
variant=geometry!=(8,32,4)
parent=ROOT/'work'/('complete-soc-'+identifier if variant else'complete-soc');parent.mkdir(parents=True,exist_ok=True);proofs=[]
for shared in[False,True]:
 mode='shared'if shared else'private';case=parent/mode;case.mkdir(exist_ok=True);commands=[]
 backend=ROOT/'rtl'/('axi_accel_shared_backend.sv'if shared else'axi_accel_complete_backend.sv')
 files=[Path(x)for x in json.loads((ROOT/'hardfloat-files.json').read_text())]+[ROOT/'rtl'/x for x in[
  'fp32_vector.sv','axi_vector_engine.sv','axi_reorder_engine.sv','fp32_layernorm.sv','axi_layernorm_engine.sv','fp32_exp.sv','fp32_softmax_row.sv','axi_softmax_engine.sv',
  'axi_dma_reader.sv','axi_dma_writer.sv','axi_dma_copy.sv','axi_dma_arbiter.sv','axi_accel_mmio.sv','gelu_lut_stream.sv','axi_gelu_engine.sv','axi_affine_gemm.sv','quant_lookup8.v','axi_quant_engine.sv','affine_array.v']]+[backend]
 frozen=files+[ROOT/'c'/n for n in['accel_io.c','accel_io.h','complete_soc_main.c','layernorm_soc_main.c','softmax_soc_main.c','accel_soc_main.c','gelu_soc_main.c']]+[ROOT/'tests/complete_soc_monitor.sv']
 hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest()for p in frozen}
 snapshot={}
 if variant:
  # Isolated test reference changes only the expected number of K-block
  # writes, which is ROWS*16. Original C files and old proofs stay unchanged.
  cdir=case/'c';cdir.mkdir(exist_ok=True)
  for name in['accel_io.c','accel_io.h','complete_soc_main.c','layernorm_soc_main.c','softmax_soc_main.c','accel_soc_main.c','gelu_soc_main.c']:
   data=(ROOT/'c'/name).read_bytes()
   if name=='accel_soc_main.c':
    old=b'((k+127)/128)';assert data.count(old)==1
    data=data.replace(old,('((k+%d)/%d)'%(args.rows*16-1,args.rows*16)).encode())
   target=cdir/name;snapshot[target]=data;hashes[str(target)]=hashlib.sha256(data).hexdigest()
 if args.resume and(case/'summary.json').exists():
  proof=json.loads((case/'summary.json').read_text());assert proof['status']=='PASS'and proof['source_sha256']==hashes
  for target,data in snapshot.items():assert target.read_bytes()==data
  proofs.append(proof);continue
 assert not(case/'summary.json').exists(),'preserve completed proof, use --resume'
 def run(argv,label,allow_failure=False):
  print('COMPLETE_SOC_PHASE '+mode+' '+label,flush=True);argv=list(map(str,argv));begin=time.monotonic()
  with(case/(label+'.log')).open('w')as log:
   p=subprocess.Popen(argv,cwd=case,stdout=log,stderr=subprocess.STDOUT)
   while True:
    try:code=p.wait(timeout=30);break
    except subprocess.TimeoutExpired:
     print('COMPLETE_SOC_RUNNING '+mode+' '+label,flush=True)
     if time.monotonic()-begin>1800:p.terminate();p.wait(timeout=20);raise TimeoutError(label)
  commands.append({'label':label,'argv':argv,'returncode':code,'elapsed_s':time.monotonic()-begin})
  if code and not allow_failure:raise RuntimeError(str(case/(label+'.log')))
  return(case/(label+'.log')).read_text(errors='replace')
 source=ROOT/'work/accel-soc'
 for target,data in snapshot.items():target.write_bytes(data)
 for name in['soc_accel_top.sv','tb_soc.sv','axi_ram_model.sv','gelu_sample.h','quant_sample.h']:shutil.copyfile(source/name,case/name)
 p=case/'soc_accel_top.sv';text=p.read_text();assert text.count(".SUPPORTED_OPS(32'h1e)")==1
 text=text.replace(".SUPPORTED_OPS(32'h1e)",".SUPPORTED_OPS(32'h1fe)")
 if variant:
  needle='axi_accel_backend #(.ID_WIDTH(`RV_LSU_BUS_TAG)) u_backend(';assert text.count(needle)==1
  text=text.replace(needle,'axi_accel_backend #(.ROWS(%d),.COLS(%d),.LANES(%d),.ID_WIDTH(`RV_LSU_BUS_TAG)) u_backend('%geometry)
 p.write_text(text)
 p=case/'axi_ram_model.sv';p.write_text(p.read_text().replace('ACCEL_SOC_','COMPLETE_SOC_'))
 # Private backend has no pool signal; preserve the same idle-FP assertion.
 monitor=case/'monitor.sv';text=(ROOT/'tests/complete_soc_monitor.sv').read_text()
 if not shared:text=text.replace('tb_soc.dut.u_backend.fp_valid',"1'b0")
 monitor.write_text(text)
 prefix='/home/zhz/rtl_tools/xpack-riscv-none-elf-gcc-14.2.0-3/bin/riscv-none-elf-'
 croot=case/'c'if variant else ROOT/'c'
 run([prefix+'gcc','-march=rv32imc_zicsr','-mabi=ilp32','-O2','-ffreestanding','-fno-builtin','-ffp-contract=off','-msmall-data-limit=0','-nostartfiles','-Wl,--no-relax','-Wl,-T,'+str(PROJECT/'sw/link.ld'),
  '-I'+str(croot),'-I'+str(case),PROJECT/'sw/start.S',croot/'accel_io.c',croot/'complete_soc_main.c','-lm','-lc','-lgcc','-o',case/'program.elf'],'firmware')
 run([prefix+'objcopy','-O','binary',case/'program.elf',case/'program.bin'],'objcopy');(case/'program.hex').write_text(''.join('%02x\n'%v for v in(case/'program.bin').read_bytes()))
 symbols=run([prefix+'nm','-S',case/'program.elf'],'symbols');addresses={}
 for name,size in [('vector_kernel_cycles',128),('vector_engine_results',96),('vector_failure',4),('reorder_kernel_cycles',48),('reorder_engine_results',36),('reorder_failure',4),('complete_stage',4)]:
  m=re.search(r'^([0-9a-f]+)\s+([0-9a-f]+)\s+[Bb]\s+'+name+'$',symbols,re.M);assert m and int(m[2],16)==size;addresses[name]=m[1]
 run(['/usr/bin/gcc','-fPIC','-c',TASK/'my_design/compat/pthread_yield_compat.c','-o',case/'compat.o'],'compat')
 run(['/home/zhz/miniforge3/bin/python3',TASK/'scripts/vcs_vm.py','-full64','-sverilog','-timescale=1ns/1ps','-debug_access','-assert','svaext',
  '+define+VCS','+define+PE_REFERENCE_MACRO','+define+AXI_RX_TIMING_CHECK','+define+AXI_RAM_OUTPUT_DELAY_NS=1.0','+incdir+'+str(HF),'+incdir+'+str(HF/'RISCV'),
  '-f',PROJECT/'build/rtl.f',*files,case/'soc_accel_top.sv',PROJECT/'tb/sram_reference_model.v',case/'axi_ram_model.sv',case/'tb_soc.sv',monitor,
  '-top','tb_soc','-top','complete_soc_monitor','-Mdir='+str(case/'csrc'),'-o',case/'simv','-LDFLAGS',case/'compat.o'],'compile')
 digest=hashlib.sha256((case/'simv').read_bytes()).hexdigest()
 plus={'VECTOR_CYCLES':'vector_kernel_cycles','VECTOR_RESULTS':'vector_engine_results','VECTOR_FAILURE':'vector_failure','REORDER_CYCLES':'reorder_kernel_cycles','REORDER_RESULTS':'reorder_engine_results','REORDER_FAILURE':'reorder_failure','COMPLETE_STAGE':'complete_stage'}
 argv=[case/'simv','+MEM_HEX='+str(case/'program.hex'),'+MAX_CYCLES=100000000','+REPORT='+str(case/'report.txt')]+['+'+key+'='+addresses[name]for key,name in plus.items()]
 normal=run(argv,'normal');assert 'COMPLETE_SOC_PASS commands=22'in normal and'COMPLETE_SOC_CPU_RETURN_PASS'in normal and not re.search(r'Fatal:|Error-\[|AXI_RX_(?:SETUP_FAIL|UNKNOWN)',normal)
 negative=run([*argv,'+INJECT_VECTOR_ERROR'],'negative',True);assert'COMPLETE_SOC_NEGATIVE_PASS'in negative and'COMPLETE_SOC_CPU_RETURN_FAIL code=bad00120'in negative and'COMPLETE_SOC_CPU_RETURN_PASS'not in negative
 for p,want in hashes.items():assert hashlib.sha256(Path(p).read_bytes()).hexdigest()==want
 assert hashlib.sha256((case/'simv').read_bytes()).hexdigest()==digest
 results=[]
 for line in(case/'report.txt').read_text().splitlines():
  m=re.fullmatch(r'(VECTOR|REORDER) (\d+) CPU=(\d+) DMA=(\d+) ENGINE=(\d+) BYTES=(\d+)',line);assert m
  tag=m[1];i,cpu,dma,eng,nb=map(int,m.groups()[1:]);results.append({'engine':tag,'case':i,'CPU_cycles':cpu,'RV32_accelerator_call_cycles':dma,'engine_cycles':eng,'bytes':nb,'kernel_speedup':cpu/dma})
 assert len(results)==11
 engine_trace=[list(map(int,m.groups()))for m in re.finditer(r'COMPLETE_SOC_ENGINE_DONE command=(\d+) cycles=(\d+) bytes=(\d+) error=(\d+)',normal)];assert len(engine_trace)==22
 fixture_names=['soc_accel_top.sv','tb_soc.sv','axi_ram_model.sv','monitor.sv','gelu_sample.h','quant_sample.h','program.elf','program.bin','program.hex']
 proof={'status':'PASS','shared_FP32':shared,'results':results,'engine_trace':engine_trace,'commands':commands,'source_sha256':hashes,'simulator_sha256':digest,'clock_period_ns':10,'RAM_response_delay_ns':1,'SRAM_macros':args.rows*args.cols,'geometry':{'rows':args.rows,'columns':args.cols,'lanes':args.lanes,'K_rows':args.rows*16},
  'fixture_sha256':{name:hashlib.sha256((case/name).read_bytes()).hexdigest()for name in fixture_names},
  'negative_control_detected':True,'full_model':False,'scope':'real RV32 all8-engine interleaving with byte guards and same-order CPU references; new vector/reorder drivers exercised; no whole-model inference or SoC PPA'}
 (case/'summary.json').write_text(json.dumps(proof,indent=2)+'\n');proofs.append(proof)
assert proofs[0]['engine_trace']==proofs[1]['engine_trace'],'FP sharing changed same-firmware engine cycles'
(parent/'summary.json').write_text(json.dumps({'status':'PASS','identical_engine_cycles':True,'completed':proofs},indent=2)+'\n');print('COMPLETE_RV32_ALL8_PRIVATE_SHARED_PASS',flush=True)
