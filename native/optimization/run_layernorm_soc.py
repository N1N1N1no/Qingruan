"""Real RV32 software/reference + unified MAC/GELU/Q/Softmax SRAM ownership."""
import hashlib,json,re,shutil,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent;BASE=ROOT.parents[1]
TASK=BASE/'native/audits/20261107/riscv_pe_contest/contest/task1';PROJECT=TASK/'my_design/project';HF=ROOT/'vendor/HardFloat-1/source'
case=ROOT/'work/layernorm-soc';case.mkdir(parents=True,exist_ok=True)
assert not(case/'summary.json').exists(),'preserve existing completed proof'
commands=[]
def run(argv,label,allow_failure=False):
 print('LAYERNORM_SOC_PHASE='+label,flush=True);argv=list(map(str,argv));begin=time.monotonic()
 with(case/(label+'.log')).open('w')as log:
  p=subprocess.Popen(argv,cwd=case,stdout=log,stderr=subprocess.STDOUT)
  while True:
   try:code=p.wait(timeout=30);break
   except subprocess.TimeoutExpired:
    print('LAYERNORM_SOC_RUNNING '+label,flush=True)
    if time.monotonic()-begin>1800:p.terminate();p.wait(timeout=20);raise TimeoutError(label)
 commands.append({'label':label,'argv':argv,'returncode':code,'elapsed_s':time.monotonic()-begin})
 if code and not allow_failure:raise RuntimeError(str(case/(label+'.log')))
 return(case/(label+'.log')).read_text(errors='replace')
run([sys.executable,ROOT/'build_accel_backend.py','--with-layernorm'],'backend')
# Keep the previous prototype fixture and its source unchanged.
source=ROOT/'work/accel-soc'
for name in['soc_accel_top.sv','tb_soc.sv','axi_ram_model.sv','gelu_sample.h','quant_sample.h']:
 assert(source/name).is_file();shutil.copyfile(source/name,case/name)
p=case/'soc_accel_top.sv';text=p.read_text();assert text.count(".SUPPORTED_OPS(32'h1e)")==1;p.write_text(text.replace(".SUPPORTED_OPS(32'h1e)",".SUPPORTED_OPS(32'h7e)"))
p=case/'axi_ram_model.sv';p.write_text(p.read_text().replace('ACCEL_SOC_','LAYERNORM_SOC_'))
prefix='/home/zhz/rtl_tools/xpack-riscv-none-elf-gcc-14.2.0-3/bin/riscv-none-elf-'
run([prefix+'gcc','-march=rv32imc_zicsr','-mabi=ilp32','-O2','-ffreestanding','-fno-builtin','-ffp-contract=off','-msmall-data-limit=0','-nostartfiles','-Wl,--no-relax','-Wl,-T,'+str(PROJECT/'sw/link.ld'),
 '-I'+str(ROOT/'c'),'-I'+str(case),PROJECT/'sw/start.S',ROOT/'c/accel_io.c',ROOT/'c/layernorm_soc_main.c','-lm','-lc','-lgcc','-o',case/'program.elf'],'firmware')
run([prefix+'objcopy','-O','binary',case/'program.elf',case/'program.bin'],'objcopy');(case/'program.hex').write_text(''.join('%02x\n'%v for v in(case/'program.bin').read_bytes()))
symbols=run([prefix+'nm','-S',case/'program.elf'],'symbols');addresses={}
for name,size in [('layernorm_kernel_cycles',48),('layernorm_engine_results',36),('layernorm_failure',4)]:
 m=re.search(r'^([0-9a-f]+)\s+([0-9a-f]+)\s+[Bb]\s+'+name+'$',symbols,re.M);assert m and int(m[2],16)==size;addresses[name]=m[1]
run(['/usr/bin/gcc','-fPIC','-c',TASK/'my_design/compat/pthread_yield_compat.c','-o',case/'compat.o'],'compat')
files=[Path(x)for x in json.loads((ROOT/'hardfloat-files.json').read_text())]+[ROOT/'rtl'/x for x in[
 'fp32_layernorm.sv','axi_layernorm_engine.sv','fp32_exp.sv','fp32_softmax_row.sv','axi_softmax_engine.sv','axi_dma_reader.sv','axi_dma_writer.sv','axi_dma_copy.sv','axi_dma_arbiter.sv','axi_accel_mmio.sv',
 'gelu_lut_stream.sv','axi_gelu_engine.sv','axi_affine_gemm.sv','quant_lookup8.v','axi_quant_engine.sv','axi_accel_layernorm_backend.sv','affine_array.v']]
frozen=files+[ROOT/'c'/n for n in['accel_io.c','accel_io.h','layernorm_soc_main.c','softmax_soc_main.c','accel_soc_main.c','gelu_soc_main.c']]+[case/n for n in['soc_accel_top.sv','tb_soc.sv','axi_ram_model.sv','gelu_sample.h','quant_sample.h']]+[ROOT/'tests/layernorm_soc_monitor.sv']
hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest()for p in frozen}
run(['/home/zhz/miniforge3/bin/python3',TASK/'scripts/vcs_vm.py','-full64','-sverilog','-timescale=1ns/1ps','-debug_access','-assert','svaext',
 '+define+VCS','+define+PE_REFERENCE_MACRO','+define+AXI_RX_TIMING_CHECK','+define+AXI_RAM_OUTPUT_DELAY_NS=1.0','+incdir+'+str(HF),'+incdir+'+str(HF/'RISCV'),
 '-f',PROJECT/'build/rtl.f',*files,case/'soc_accel_top.sv',PROJECT/'tb/sram_reference_model.v',case/'axi_ram_model.sv',case/'tb_soc.sv',ROOT/'tests/layernorm_soc_monitor.sv',
 '-top','tb_soc','-top','layernorm_soc_monitor','-Mdir='+str(case/'csrc'),'-o',case/'simv','-LDFLAGS',case/'compat.o'],'compile')
digest=hashlib.sha256((case/'simv').read_bytes()).hexdigest()
argv=[case/'simv','+MEM_HEX='+str(case/'program.hex'),'+MAX_CYCLES=100000000','+LAYERNORM_CYCLES='+addresses['layernorm_kernel_cycles'],
 '+LAYERNORM_RESULTS='+addresses['layernorm_engine_results'],'+LAYERNORM_FAILURE='+addresses['layernorm_failure'],'+REPORT='+str(case/'report.txt')]
normal=run(argv,'normal');assert 'LAYERNORM_SOC_PASS commands=15'in normal and'LAYERNORM_SOC_CPU_RETURN_PASS'in normal and not re.search(r'Fatal:|Error-\[|AXI_RX_(?:SETUP_FAIL|UNKNOWN)',normal)
negative=run([*argv,'+INJECT_LAYERNORM_ERROR'],'negative',True);assert'LAYERNORM_SOC_NEGATIVE_PASS'in negative and'LAYERNORM_SOC_CPU_RETURN_FAIL code=bad000e4'in negative and'LAYERNORM_SOC_CPU_RETURN_PASS'not in negative
for p,want in hashes.items():assert hashlib.sha256(Path(p).read_bytes()).hexdigest()==want
assert hashlib.sha256((case/'simv').read_bytes()).hexdigest()==digest
results=[]
for line in(case/'report.txt').read_text().splitlines():
 m=re.fullmatch(r'CASE (\d+) CPU=(\d+) DMA=(\d+) ENGINE=(\d+) BYTES=(\d+)',line);assert m
 i,cpu,dma,eng,nb=map(int,m.groups());results.append({'case':i,'CPU_cycles':cpu,'RV32_accelerator_call_cycles':dma,'engine_cycles':eng,'bytes':nb,'kernel_speedup':cpu/dma})
assert len(results)==3
summary={'status':'PASS','results':results,'commands':commands,'source_sha256':hashes,'simulator_sha256':digest,'clock_period_ns':10,'RAM_response_delay_ns':1,'SRAM_macros':256,
 'negative_control_detected':True,'full_model':False,'scope':'real RV32 same-order float32 LayerNorm and MMIO/DMA; 15 interleaved MAC/GELU/Q/Softmax/LayerNorm commands, all byte guards; CPU and packing included in kernel measurements, not whole-model latency/ADP'}
(case/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print('LAYERNORM_RV32_SOC_VERIFIED',flush=True)
