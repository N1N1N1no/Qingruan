"""Real CPU+RTL full inference. A frozen-C sample PASS is not official accuracy."""
import argparse, hashlib, json, re, resource, shutil, struct, subprocess, sys, time
from pathlib import Path

ROOT=Path(__file__).resolve().parent;BASE=ROOT.parents[1]
TASK=BASE/'native/audits/20261107/riscv_pe_contest/contest/task1';PROJECT=TASK/'my_design/project'
parser=argparse.ArgumentParser();parser.add_argument('--prepare-only',action='store_true');parser.add_argument('--image',default='ILSVRC2012_val_00000274.JPEG');parser.add_argument('--geometry',choices=['R8_C16_L4','R8_C32_L4','R16_C8_L4'],default='R8_C16_L4');args=parser.parse_args()
assert re.fullmatch(r'ILSVRC2012_val_\d{8}\.JPEG',args.image)
selection=json.loads((ROOT/'phase1-selection.json').read_text());assert selection['status']=='PHASE1_MODULE_ENGINEERING_ACCEPTED_PHASE2_REQUIRED'
review=json.loads((ROOT/'phase1-review.json').read_text());assert review['phase1_accepted'] and not review['pending_phase1']
case=ROOT/'work/full-rv32'/args.geometry/args.image;case.mkdir(parents=True,exist_ok=True)
assert not(case/'summary.json').exists() and not(case/'normal.log').exists(),'preserve completed or interrupted attempt'
rows,cols,lanes=map(int,re.fullmatch(r'R(\d+)_C(\d+)_L(\d+)',args.geometry).groups())
firmware=ROOT/'work/complete-model-direct-build'/args.geometry;manifest=json.loads((firmware/'manifest.json').read_text())
def local(n):return BASE/n[11:] if n.startswith('/workspace/') else Path(n) if Path(n).is_absolute() else BASE/n
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
assert sha(ROOT/'phase1-review.json')==selection['review_sha256']
for name,want in manifest['source_sha256'].items():assert sha(local(name))==want,name
assert sha(firmware/'program.elf')==manifest['firmware_sha256']
for name in ['program.elf','program.bin','program.hex']:shutil.copyfile(firmware/name,case/name)
source=ROOT/'work/accel-soc'
for name in ['soc_accel_top.sv','tb_soc.sv','axi_ram_model.sv']:shutil.copyfile(source/name,case/name)
p=case/'soc_accel_top.sv';text=p.read_text();assert text.count(".SUPPORTED_OPS(32'h1e)")==1
text=text.replace(".SUPPORTED_OPS(32'h1e)",".SUPPORTED_OPS(32'h1fe)");needle='axi_accel_backend #(.ID_WIDTH(`RV_LSU_BUS_TAG)) u_backend(';assert text.count(needle)==1
p.write_text(text.replace(needle,'axi_accel_backend #(.ROWS(%d),.COLS(%d),.LANES(%d),.ID_WIDTH(`RV_LSU_BUS_TAG)) u_backend('%(rows,cols,lanes)))
p=case/'tb_soc.sv';text=p.read_text().replace('integer max_cycles = 200000;','longint unsigned max_cycles = 6000000000;').replace('integer cycles = 0;','longint unsigned cycles = 0;');p.write_text(text)
p=case/'axi_ram_model.sv';text=p.read_text();assert text.count('parameter MEM_BYTES=262144')==2
p.write_text(text.replace('parameter MEM_BYTES=262144','parameter MEM_BYTES=67108864').replace('ACCEL_SOC_','FULL_MODEL_'))
plan_dir=ROOT/'work/fused-plan';plan=json.loads((plan_dir/'plan.json').read_text());memory=plan['memory'];graph=(plan_dir/'graph.bin').read_bytes();header=struct.unpack_from('<16I',graph)
assert header[3]==1396 and header[4]==77 and header[14]==1
output=next(x for x in plan['tensors']if x['id']==header[6]);input_tensor=next(x for x in plan['tensors']if x['id']==header[5]);assert output['bytes']==4000 and output['dtype']=='float32'
fusion_base=memory['descriptor_base']+(len(graph)+63)//64*64;fusion_bytes=(plan_dir/'gelu-fusions.bin').read_bytes();assert len(fusion_bytes)%28==0
sections=[(memory['firmware_base'],(case/'program.bin').read_bytes()),(memory['descriptor_base'],graph),(fusion_base,fusion_bytes),(memory['constant_base'],(plan_dir/'constants.bin').read_bytes()),(memory['activation_base']+input_tensor['offset'],(ROOT/'work/fusion-proof'/(args.image+'.input')).read_bytes()),(memory['gelu_table_base'],(ROOT/'work/lookup-tables/gelu.bin').read_bytes()),(memory['quant_table_base'],(ROOT/'work/lookup-tables/quant-thresholds.bin').read_bytes()),(0x83fff000,(ROOT/'work/fusion-proof'/(args.image+'.baseline')).read_bytes())]
sections.sort()
for (address,data),(next_address,_)in zip(sections,sections[1:]):assert address+len(data)<=next_address
assert all(0x80000000<=a and a+len(d)<=0x84000000 for a,d in sections)
assert len(sections[-1][1])==4000
with(case/'data.hex').open('w')as stream:
 for address,data in sections:
  stream.write('@%08x\n'%(address-0x80000000))
  for start in range(0,len(data),65536):stream.write(''.join('%02x\n'%x for x in data[start:start+65536]))
compiler=Path('/home/zhz/rtl_tools/xpack-riscv-none-elf-gcc-14.2.0-3/bin/riscv-none-elf-nm')
if not compiler.is_file():compiler=BASE/'native/rootfs/home/zhz/rtl_tools/xpack-riscv-none-elf-gcc-14.2.0-3/bin/riscv-none-elf-nm'
symbols=subprocess.check_output([str(compiler),'-S',str(case/'program.elf')],text=True);(case/'symbols.txt').write_text(symbols)
variables={'NODE_CYCLES':'node_cycles','INFERENCE_CYCLES':'inference_cycles','ERROR_NODE':'model_error_node','MAC_JOBS':'model_mac_jobs','FUSIONS':'model_gelu_fusions','MAC_COMMANDS':'model_mac_commands','QUANT_COMMANDS':'model_quant_commands','TOP1':'model_top1','EXPECTED_TOP1':'model_expected_top1','MAX_ERROR':'model_max_error_bits'}
addresses={}
for key,name in variables.items():
 m=re.search(r'^([0-9a-f]+)\s+([0-9a-f]+)\s+[BbDd]\s+'+name+'$',symbols,re.M);assert m,name;addresses[key]=m[1]
nonmac=json.loads((ROOT/'work/nonmac-plan/summary.json').read_text());mac=json.loads((ROOT/'work/mac-plan/summary.json').read_text());expected_commands=nonmac['total_commands']+mac['MAC_batches']+mac['hardware_im2col_commands']+166+len(fusion_bytes)//28
node_events=1396-sum(struct.unpack_from('<7I',fusion_bytes,start)[1]-struct.unpack_from('<7I',fusion_bytes,start)[0] for start in range(0,len(fusion_bytes),28))
parameters={**addresses,'ARENA_LO':'%x'%memory['activation_base'],'ARENA_HI':'%x'%(memory['activation_base']+memory['activation_arena_bytes']),'OUTPUT_ADDR':'%x'%(memory['activation_base']+output['offset']),'EXPECTED_ADDR':'83fff000','EXPECTED_COMMANDS':str(expected_commands),'NODES':'1396','EXPECTED_FUSIONS':str(len(fusion_bytes)//28),'EXPECTED_NODE_EVENTS':str(node_events)}
hf=ROOT/'vendor/HardFloat-1/source';files=[local(x)for x in json.loads((ROOT/'hardfloat-files.json').read_text())]+[ROOT/'rtl'/n for n in ['fp32_vector.sv','axi_vector_engine.sv','axi_reorder_engine.sv','fp32_layernorm.sv','axi_layernorm_engine.sv','fp32_exp.sv','fp32_softmax_row.sv','axi_softmax_engine.sv','axi_dma_reader.sv','axi_dma_writer.sv','axi_dma_copy.sv','axi_dma_arbiter.sv','axi_accel_mmio.sv','gelu_lut_stream.sv','axi_gelu_engine.sv','axi_affine_gemm.sv','quant_lookup8.v','axi_quant_engine.sv','affine_array.v','axi_accel_shared_backend.sv']]
monitor=ROOT/'tests/full_model_monitor.sv';cpu_files=[]
for line in(PROJECT/'build/rtl.f').read_text().splitlines():
 if line.startswith('/'):cpu_files.append(local(line))
 elif line.startswith('+incdir+'):
  directory=local(line[len('+incdir+'):]);cpu_files.extend(p for p in directory.rglob('*')if p.suffix in['.vh','.svh'])
inputs=[Path(__file__),monitor,ROOT/'phase1-selection.json',ROOT/'phase1-review.json',firmware/'manifest.json',PROJECT/'build/rtl.f',ROOT/'c/accel_io.h',BASE/'native/swin-tiny/c/graph.h',BASE/'native/swin-tiny/c/pe_driver.h',*files,*cpu_files,*map(local,manifest['source_sha256']),*(case/x for x in ['soc_accel_top.sv','tb_soc.sv','axi_ram_model.sv','program.elf','program.bin','program.hex','data.hex'])]
hashes={str(p):sha(p)for p in dict.fromkeys(inputs)}
preparation={'status':'FULL_RV32_FIXTURES_AND_RAM_IMAGE_READY_EXECUTION_PENDING','image':args.image,'geometry':{'rows':rows,'columns':cols,'lanes':lanes},'parameters':parameters,'logical_external_RAM_bytes':67108864,'instruction_memory_model':'identical initial read-only instruction mirror; IFU restricted to firmware region; no self-modifying code','sections':[{'address':hex(a),'bytes':len(d),'sha256':hashlib.sha256(d).hexdigest()}for a,d in sections],'source_sha256':hashes,'scope':'actual CPU+all8 RTL complete-inference fixture; no execution or official accuracy acceptance from preparation'}
(case/'preparation.json').write_text(json.dumps(preparation,indent=2)+'\n')
if args.prepare_only:print('FULL_RV32_PREPARED image='+args.image+' expected_commands='+str(expected_commands)+' node_events='+str(node_events),flush=True);sys.exit(0)
records=[]
def run(argv,label,deadline):
 print('FULL_RV32_PHASE '+label,flush=True);start=time.monotonic();argv=list(map(str,argv))
 with(case/(label+'.log')).open('w')as log:
  def memory_limit():resource.setrlimit(resource.RLIMIT_AS,(8*1024**3,8*1024**3))
  process=subprocess.Popen(argv,cwd=case,stdout=log,stderr=subprocess.STDOUT,preexec_fn=memory_limit)
  (case/'live-process.json').write_text(json.dumps({'label':label,'pid':process.pid,'argv':argv,'status':'RUNNING'},indent=2)+'\n')
  while True:
   try:code=process.wait(timeout=30);break
   except subprocess.TimeoutExpired:
    print('FULL_RV32_RUNNING '+label+' elapsed_s=%d'%(time.monotonic()-start),flush=True)
    if time.monotonic()-start>deadline:process.terminate();process.wait(timeout=30);raise TimeoutError(label)
 records.append({'phase':label,'returncode':code,'elapsed_s':time.monotonic()-start,'argv':argv});(case/'live-process.json').write_text(json.dumps({'label':label,'status':'TERMINAL','returncode':code},indent=2)+'\n')
 return code,(case/(label+'.log')).read_text(errors='replace')
code,_=run(['/usr/bin/gcc','-fPIC','-c',TASK/'my_design/compat/pthread_yield_compat.c','-o',case/'compat.o'],'compat',120);assert code==0
code,_=run(['/home/zhz/miniforge3/bin/python3',TASK/'scripts/vcs_vm.py','-full64','-sverilog','-timescale=1ns/1ps','-debug_access','-assert','svaext','+define+VCS','+define+PE_REFERENCE_MACRO','+define+AXI_RX_TIMING_CHECK','+define+AXI_RAM_OUTPUT_DELAY_NS=1.0','+incdir+'+str(hf),'+incdir+'+str(hf/'RISCV'),'-f',PROJECT/'build/rtl.f',*files,case/'soc_accel_top.sv',PROJECT/'tb/sram_reference_model.v',case/'axi_ram_model.sv',case/'tb_soc.sv',monitor,'-top','tb_soc','-top','full_model_monitor','-Mdir='+str(case/'csrc'),'-o',case/'simv','-LDFLAGS',case/'compat.o'],'compile',1800);assert code==0
simulator_hash=sha(case/'simv')
code,normal=run([case/'simv','+MEM_HEX='+str(case/'data.hex'),'+MAX_CYCLES=6000000000','+REPORT='+str(case/'report.txt'),'+LOGITS='+str(case/'logits.bin'),*['+'+k+'='+v for k,v in parameters.items()]],'normal',86400)
for name,want in hashes.items():assert sha(Path(name))==want,name
assert sha(case/'simv')==simulator_hash
passed=code==0 and 'FULL_MODEL_RV32_PASS'in normal and 'FULL_MODEL_CPU_RETURN_PASS'in normal and not re.search(r'Fatal:|Error-\[|FULL_MODEL_FAIL|AXI_RX_(?:SETUP_FAIL|UNKNOWN)',normal)
result={'status':'FULL_RV32_SINGLE_IMAGE_FROZEN_C_PASS_OFFICIAL_ACCURACY_OPEN'if passed else'FULL_RV32_DIAGNOSTIC_FAILURE','full_RV32_executed':True,'image':args.image,'geometry':args.geometry,'all8_engines_participation_asserted':passed,'official_accuracy_accepted':False,'final_SoC_ADP_accepted':False,'source_sha256':hashes,'simulator_sha256':simulator_hash,'commands':records,'scope':'real RV32 CPU, exact all8 RTL, MMIO/DMA/scheduling/remaining integer metadata; full graph and all1000 frozen-C logits checked by original C criterion. First image only, not organizer accuracy, SoC synthesis or gate/SDF acceptance. Compute-loop counter includes final logits written to external RAM; CPU-return counter also includes startup and validation.'}
if(case/'report.txt').exists():result['report']=(case/'report.txt').read_text()
if(case/'logits.bin').exists():result['logits_sha256']=sha(case/'logits.bin');result['logits_byte_exact']=(case/'logits.bin').read_bytes()==sections[-1][1]
(case/('summary.json'if passed else'failure.json')).write_text(json.dumps(result,indent=2)+'\n')
assert passed,str(case/'failure.json');print('FULL_RV32_SINGLE_IMAGE_PASS_OFFICIAL_ACCURACY_STILL_OPEN',flush=True)
