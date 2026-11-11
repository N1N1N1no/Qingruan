import hashlib,json,os,re,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent;BASE=ROOT.parents[1]
TASK=BASE/'native/audits/20261107/riscv_pe_contest/contest/task1';PROJECT=TASK/'my_design/project';case=ROOT/'work/dma-soc'
subprocess.run([sys.executable,str(ROOT/'prepare_dma_soc.py')],check=True)
commands=[]
def run(argv,label,allow_failure=False):
 print('DMA_SOC_PHASE='+label,flush=True);argv=list(map(str,argv));start=time.monotonic()
 with (case/(label+'.log')).open('w') as f:r=subprocess.run(argv,cwd=case,stdout=f,stderr=subprocess.STDOUT,timeout=1800)
 commands.append({'argv':argv,'label':label,'returncode':r.returncode,'elapsed_s':time.monotonic()-start})
 if r.returncode and not allow_failure:raise RuntimeError(str(case/(label+'.log')))
 return (case/(label+'.log')).read_text(errors='replace')
prefix='/home/zhz/rtl_tools/xpack-riscv-none-elf-gcc-14.2.0-3/bin/riscv-none-elf-'
run([prefix+'gcc','-march=rv32imc_zicsr','-mabi=ilp32','-O2','-ffreestanding','-fno-builtin','-msmall-data-limit=0','-nostartfiles',
     '-Wl,--no-relax','-Wl,-T,'+str(PROJECT/'sw/link.ld'),'-Wl,-Map,'+str(case/'program.map'),'-I'+str(ROOT/'c'),
     PROJECT/'sw/start.S',ROOT/'c/accel_io.c',ROOT/'c/dma_soc_main.c','-lc','-lgcc','-o',case/'program.elf'],'rv32_compile')
run([prefix+'objcopy','-O','binary',case/'program.elf',case/'program.bin'],'objcopy')
(case/'program.hex').write_text('\n'.join('%02x'%v for v in (case/'program.bin').read_bytes())+'\n')
symbols=run([prefix+'nm','-S',case/'program.elf'],'symbols')
addresses={}
for name,size in [('dma_kernel_cycles',64),('dma_engine_results',48),('dma_failure',4)]:
 m=re.search(r'^([0-9a-f]+)\s+([0-9a-f]+)\s+[Bb]\s+'+name+'$',symbols,re.M)
 assert m and int(m[2],16)==size,name;addresses[name]=m[1]
run(['/usr/bin/gcc','-fPIC','-c',TASK/'my_design/compat/pthread_yield_compat.c','-o',case/'compat.o'],'compat')
rtl=[ROOT/'rtl'/(x+'.sv') for x in ['axi_dma_reader','axi_dma_writer','axi_dma_copy','axi_dma_arbiter','axi_accel_mmio']]
run(['/home/zhz/miniforge3/bin/python3',TASK/'scripts/vcs_vm.py','-full64','-sverilog','-timescale=1ns/1ps','-debug_access',
     '+define+VCS','+define+PE_REFERENCE_MACRO','+define+AXI_RX_TIMING_CHECK','+define+AXI_RAM_OUTPUT_DELAY_NS=1.0','-assert','svaext',
     '-f',PROJECT/'build/rtl.f',*rtl,case/'soc_dma_top.sv',PROJECT/'tb/sram_reference_model.v',case/'axi_ram_model.sv',case/'tb_soc.sv',ROOT/'tests/dma_soc_monitor.sv',
     '-top','tb_soc','-top','dma_soc_monitor','-Mdir='+str(case/'csrc'),'-o',case/'simv','-LDFLAGS',case/'compat.o'],'compile')
digest=hashlib.sha256((case/'simv').read_bytes()).hexdigest()
argv=[case/'simv','+MEM_HEX='+str(case/'program.hex'),'+CYCLES_ADDR='+addresses['dma_kernel_cycles'],'+RESULTS_ADDR='+addresses['dma_engine_results'],
      '+FAILURE_ADDR='+addresses['dma_failure'],'+MAX_CYCLES=25000000']
normal=run([*argv,'+REPORT='+str(case/'normal-report.txt')],'normal')
assert normal.count('DMA_SOC_MONITOR_PASS')==1 and normal.count('DMA_SOC_CPU_RETURN_PASS')==1 and not re.search(r'Fatal:|Error-\[|AXI_RX_(?:SETUP_FAIL|UNKNOWN)',normal),normal[-2000:]
negative=run([*argv,'+INJECT_DMA_ERROR','+REPORT='+str(case/'negative-report.txt')],'negative',True)
assert 'DMA_SOC_MONITOR_NEGATIVE_PASS' in negative and 'DMA_SOC_CPU_RETURN_FAIL code=bad00040' in negative and 'DMA_SOC_CPU_RETURN_PASS' not in negative,negative[-2000:]
assert hashlib.sha256((case/'simv').read_bytes()).hexdigest()==digest
results=[]
for row in (case/'normal-report.txt').read_text().splitlines():
 m=re.fullmatch(r'CASE (\d+) CPU=(\d+) DMA=(\d+) ENGINE=(\d+) BYTES=(\d+)',row);assert m,row
 i,cpu,dma,eng,n=map(int,m.groups());results.append({'case':i,'CPU_memcpy_cycles':cpu,'RISC_V_DMA_call_cycles':dma,'engine_cycles':eng,'bytes':n,'kernel_speedup':cpu/dma})
assert len(results)==4
summary={'status':'PASS','scope':'actual RV32 + original CPU/PE SoC plus DMA/MMIO/arbiter integration; four copy microkernels, not complete Swin inference or optimized SoC area',
 'legacy_PE_retained_for_integration_fixture':True,'full_model':False,'sdf':False,'clock_period_ns':10,'RAM_response_delay_ns':1,'negative_control_detected':True,
 'results':results,'simulator_sha256':digest,'commands':commands,'normal_monitor':re.search(r'DMA_SOC_MONITOR_PASS[^\n]+',normal)[0],
 'input_sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [*rtl,ROOT/'c/accel_io.c',ROOT/'c/dma_soc_main.c',ROOT/'tests/dma_soc_monitor.sv',case/'soc_dma_top.sv',case/'tb_soc.sv',case/'axi_ram_model.sv']}}
(case/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print('DMA_RV32_SOC_PASS',flush=True)
