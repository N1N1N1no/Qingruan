"""RV32 Slice/Concat and sequential AvgPool operator tests, no model inference."""
import argparse,hashlib,json,re,shutil,subprocess,time,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent;BASE=ROOT.parents[1];SWIN=BASE/'native/swin-tiny'
TASK=BASE/'native/audits/20261107/riscv_pe_contest/contest/task1';PROJECT=TASK/'my_design/project';HF=ROOT/'vendor/HardFloat-1/source'
parser=argparse.ArgumentParser();parser.add_argument('--prepare-only',action='store_true');args=parser.parse_args()
case=ROOT/'work/aux-soc';case.mkdir(parents=True,exist_ok=True);assert not(case/'summary.json').exists(),'preserve completed proof'
def run(argv,label,allow_failure=False):
 print('AUX_SOC_PHASE '+label,flush=True);started=time.monotonic()
 with(case/(label+'.log')).open('w')as f:
  p=subprocess.Popen(list(map(str,argv)),cwd=case,stdout=f,stderr=subprocess.STDOUT)
  while True:
   try:code=p.wait(timeout=30);break
   except subprocess.TimeoutExpired:
    print('AUX_SOC_RUNNING '+label,flush=True)
    if time.monotonic()-started>1800:p.terminate();p.wait(timeout=20);raise TimeoutError(label)
 if code and not allow_failure:raise RuntimeError(str(case/(label+'.log')))
 return(case/(label+'.log')).read_text(errors='replace')
source=ROOT/'work/accel-soc'
for name in['soc_accel_top.sv','tb_soc.sv','axi_ram_model.sv']:shutil.copyfile(source/name,case/name)
p=case/'soc_accel_top.sv';text=p.read_text().replace(".SUPPORTED_OPS(32'h1e)",".SUPPORTED_OPS(32'h1fe)")
needle='axi_accel_backend #(.ID_WIDTH(`RV_LSU_BUS_TAG)) u_backend(';assert text.count(needle)==1
p.write_text(text.replace(needle,'axi_accel_backend #(.ROWS(8),.COLS(16),.LANES(4),.ID_WIDTH(`RV_LSU_BUS_TAG)) u_backend('))
p=case/'axi_ram_model.sv';text=p.read_text().replace('ACCEL_SOC_','AUX_SOC_');assert text.count('parameter MEM_BYTES=262144')==2;p.write_text(text.replace('parameter MEM_BYTES=262144','parameter MEM_BYTES=1048576'))
linker=case/'link.ld';text=(PROJECT/'sw/link.ld').read_text();assert text.count('LENGTH = 256K')==1;linker.write_text(text.replace('LENGTH = 256K','LENGTH = 1024K'))
compiler=Path('/home/zhz/rtl_tools/xpack-riscv-none-elf-gcc-14.2.0-3/bin/riscv-none-elf-gcc')
if not compiler.is_file():compiler=BASE/'native/rootfs/home/zhz/rtl_tools/xpack-riscv-none-elf-gcc-14.2.0-3/bin/riscv-none-elf-gcc'
prefix=str(compiler)[:-3]
cfiles=[ROOT/'c'/x for x in['nonmac_plan.c','aux_plan.c','nonmac_dispatch.c','accel_io.c','aux_soc_main.c']]
run([prefix+'gcc','-march=rv32imc_zicsr','-mabi=ilp32','-O2','-ffreestanding','-fno-builtin','-ffp-contract=off','-msmall-data-limit=0','-nostartfiles','-Wl,--no-relax','-Wl,-T,'+str(linker),'-Wl,-Map,'+str(case/'program.map'),
 '-I'+str(SWIN/'c'),'-I'+str(ROOT/'c'),PROJECT/'sw/start.S',*cfiles,'-lm','-lc','-lgcc','-o',case/'program.elf'],'firmware')
run([prefix+'objcopy','-O','binary',case/'program.elf',case/'program.bin'],'objcopy');(case/'program.hex').write_text(''.join('%02x\n'%v for v in(case/'program.bin').read_bytes()))
symbols=run([prefix+'nm','-S',case/'program.elf'],'symbols');addresses={}
for name in['aux_stage','aux_failure','aux_commands','aux_streamed_bytes','aux_kernel_cycles','aux_pool_results','aux_out_lo','aux_out_hi','aux_work_lo','aux_work_hi']:
 m=re.search(r'^([0-9a-f]+)\s+([0-9a-f]+)\s+[Bb]\s+'+name+'$',symbols,re.M);assert m;addresses[name]=m[1]
def local(x):return BASE/x[11:]if x.startswith('/workspace/')else Path(x)
files=[local(x)for x in json.loads((ROOT/'hardfloat-files.json').read_text())]+[ROOT/'rtl'/x for x in[
 'fp32_vector.sv','axi_vector_engine.sv','axi_reorder_engine.sv','fp32_layernorm.sv','axi_layernorm_engine.sv','fp32_exp.sv','fp32_softmax_row.sv','axi_softmax_engine.sv',
 'axi_dma_reader.sv','axi_dma_writer.sv','axi_dma_copy.sv','axi_dma_arbiter.sv','axi_accel_mmio.sv','gelu_lut_stream.sv','axi_gelu_engine.sv','axi_affine_gemm.sv','quant_lookup8.v','axi_quant_engine.sv','affine_array.v','axi_accel_shared_backend.sv']]
fixtures=[case/x for x in['soc_accel_top.sv','tb_soc.sv','axi_ram_model.sv','link.ld','program.elf','program.bin','program.hex']]
frozen=files+cfiles+[ROOT/'c/aux_plan.h',ROOT/'c/nonmac_plan.h',ROOT/'c/nonmac_dispatch.h',ROOT/'c/accel_io.h',SWIN/'c/graph.h',ROOT/'tests/aux_soc_monitor.sv',Path(__file__),PROJECT/'sw/start.S',PROJECT/'build/rtl.f']+fixtures
hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest()for p in frozen}
if args.prepare_only:
 (case/'preparation.json').write_text(json.dumps({'status':'FIRMWARE_AND_TEST_FIXTURES_READY_EXECUTION_PENDING','RAM_bytes':1048576,'symbols':addresses,'source_sha256':hashes,'compiler':str(compiler),'scope':'cross-compiled and linked only; no RTL compilation or simulation'},indent=2)+'\n')
 print('AUX_SOC_FIRMWARE_PREPARED_EXECUTION_PENDING',flush=True);sys.exit(0)
run(['/usr/bin/gcc','-fPIC','-c',TASK/'my_design/compat/pthread_yield_compat.c','-o',case/'compat.o'],'compat')
run(['/home/zhz/miniforge3/bin/python3',TASK/'scripts/vcs_vm.py','-full64','-sverilog','-timescale=1ns/1ps','-debug_access','-assert','svaext',
 '+define+VCS','+define+PE_REFERENCE_MACRO','+define+AXI_RX_TIMING_CHECK','+define+AXI_RAM_OUTPUT_DELAY_NS=1.0','+incdir+'+str(HF),'+incdir+'+str(HF/'RISCV'),
 '-f',PROJECT/'build/rtl.f',*files,case/'soc_accel_top.sv',PROJECT/'tb/sram_reference_model.v',case/'axi_ram_model.sv',case/'tb_soc.sv',ROOT/'tests/aux_soc_monitor.sv',
 '-top','tb_soc','-top','aux_soc_monitor','-Mdir='+str(case/'csrc'),'-o',case/'simv','-LDFLAGS',case/'compat.o'],'compile')
digest=hashlib.sha256((case/'simv').read_bytes()).hexdigest()
argv=[case/'simv','+MEM_HEX='+str(case/'program.hex'),'+MAX_CYCLES=100000000','+REPORT='+str(case/'report.txt')]+['+'+key.upper()+'='+value for key,value in addresses.items()]
normal=run(argv,'normal');assert'AUX_SOC_PASS commands=67'in normal and'AUX_SOC_CPU_RETURN_PASS'in normal and not re.search(r'Fatal:|Error-\[|AXI_RX_(?:SETUP_FAIL|UNKNOWN)',normal)
negative=run([*argv,'+INJECT_AUX_ERROR'],'negative',True);assert'AUX_SOC_NEGATIVE_PASS'in negative and'AUX_SOC_CPU_RETURN_FAIL code=bad00330'in negative and'AUX_SOC_CPU_RETURN_PASS'not in negative
for p,want in hashes.items():assert hashlib.sha256(Path(p).read_bytes()).hexdigest()==want
assert digest==hashlib.sha256((case/'simv').read_bytes()).hexdigest()
results=[]
for line in(case/'report.txt').read_text().splitlines():
 m=re.fullmatch(r'POOL (\d+) CPU=(\d+) CALL=(\d+) ENGINE=(\d+) WRITTEN_BYTES=(\d+)',line);assert m
 i,cpu,call,engine,nb=map(int,m.groups());results.append({'case':i,'rows':768 if i else 5,'inner':49 if i else 7,'CPU_cycles':cpu,'RV32_accelerator_call_cycles':call,'engine_cycles':engine,'written_bytes_all_commands':nb,'kernel_speedup':cpu/call})
assert len(results)==2
summary={'status':'PASS','commands':67,'RAM_bytes':1048576,'array_geometry':{'rows':8,'columns':16,'lanes':4,'SRAMs':128},'negative_control_detected':True,'source_sha256':hashes,'simulator_sha256':digest,'AvgPool_results':results,
 'scope':'real RV32 operators: stepped/negative-start Slice, two-input and four-input Concat with negative axis, sequential FP32 AvgPool including actual 768x49 shape; every output bit and protected memory word checked by CPU, all DMA write addresses checked by monitor; no model/image inference or complete SoC PPA'}
(case/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print('AUX_RV32_OPERATORS_PASS',flush=True)
