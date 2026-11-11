"""Match the source oracle against the actual installed RV32 erff library."""
import hashlib,json,re,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parent;BASE=ROOT.parents[1]
TASK=BASE/'native/audits/20261107/riscv_pe_contest/contest/task1';PROJECT=TASK/'my_design/project'
case=ROOT/'work/erf-cpu';case.mkdir(parents=True,exist_ok=True)
records=[tuple(int(x,16)for x in row.split())for row in (ROOT/'work/erf-reference/vectors.hex').read_text().splitlines()]
indices=list(range(14))+[14+i*(len(records)-15)//369 for i in range(370)];assert len(set(indices))==384
data=[records[i]for i in indices]
header='#define ERF_CPU_CASES 384\nconst uint32_t erf_cpu_inputs[384]={'+','.join(hex(x[0])+'u'for x in data)+'};\nconst uint32_t erf_cpu_expected[384]={'+','.join(hex(x[1])+'u'for x in data)+'};\n'
(case/'erf_cpu_vectors.h').write_text(header);(case/'golden.hex').write_text(''.join('%08x\n'%x[1]for x in data))
ram=(PROJECT/'tb/axi_ram_model.sv').read_text().replace('SOC_SMOKE_PASS: CPU checked all 64 PE output words','ERF_CPU_RETURN_PASS').replace('SOC_SMOKE_FAIL','ERF_CPU_RETURN_FAIL')
ram=ram.replace('else $fatal(1,"ERF_CPU_RETURN_FAIL code=%08x",done_value);','else begin #0; $fatal(1,"ERF_CPU_RETURN_FAIL code=%08x",done_value); end');(case/'axi_ram_model.sv').write_text(ram)
commands=[]
def run(argv,label,allow_failure=False):
 print('ERF_CPU_PHASE='+label,flush=True);argv=list(map(str,argv))
 with (case/(label+'.log')).open('w') as log:r=subprocess.run(argv,cwd=case,stdout=log,stderr=subprocess.STDOUT,timeout=1200)
 commands.append({'label':label,'argv':argv,'returncode':r.returncode})
 if r.returncode and not allow_failure:raise RuntimeError(str(case/(label+'.log')))
 return(case/(label+'.log')).read_text(errors='replace')
prefix='/home/zhz/rtl_tools/xpack-riscv-none-elf-gcc-14.2.0-3/bin/riscv-none-elf-'
run([prefix+'gcc','-march=rv32imc_zicsr','-mabi=ilp32','-O2','-ffreestanding','-fno-builtin','-ffp-contract=off','-msmall-data-limit=0','-nostartfiles','-Wl,--no-relax','-Wl,-T,'+str(PROJECT/'sw/link.ld'),'-I'+str(ROOT/'c'),'-I'+str(case),PROJECT/'sw/start.S',ROOT/'c/erf_cpu_main.c',ROOT/'c/accel_io.c','-lm','-lc','-lgcc','-o',case/'program.elf'],'compile_RV32')
run([prefix+'objcopy','-O','binary',case/'program.elf',case/'program.bin'],'objcopy');(case/'program.hex').write_text(''.join('%02x\n'%v for v in(case/'program.bin').read_bytes()))
symbols=run([prefix+'nm','-S',case/'program.elf'],'symbols');addresses={}
for name,size in [('erf_cpu_expected',1536),('erf_cpu_actual',1536),('erf_cpu_tested',4),('erf_cpu_failure',4),('erf_cpu_cycles',8)]:
 m=re.search(r'^([0-9a-f]+)\s+([0-9a-f]+)\s+[A-Za-z]\s+'+name+'$',symbols,re.M);assert m and int(m[2],16)==size;addresses[name]=m[1]
run(['/usr/bin/gcc','-fPIC','-c',TASK/'my_design/compat/pthread_yield_compat.c','-o',case/'compat.o'],'compat')
run(['/home/zhz/miniforge3/bin/python3',TASK/'scripts/vcs_vm.py','-full64','-sverilog','-timescale=1ns/1ps','-debug_access','-assert','svaext','+define+VCS','+define+PE_REFERENCE_MACRO','+define+AXI_RX_TIMING_CHECK','+define+AXI_RAM_OUTPUT_DELAY_NS=1.0','-f',PROJECT/'build/rtl.f',PROJECT/'tb/sram_reference_model.v',case/'axi_ram_model.sv',PROJECT/'tb/tb_soc.sv',ROOT/'tests/erf_cpu_monitor.sv','-top','tb_soc','-top','erf_cpu_monitor','-Mdir='+str(case/'csrc'),'-o',case/'simv','-LDFLAGS',case/'compat.o'],'compile_RTL')
digest=hashlib.sha256((case/'simv').read_bytes()).hexdigest()
argv=[case/'simv','+MEM_HEX='+str(case/'program.hex'),'+ACTUAL_ADDR='+addresses['erf_cpu_actual'],'+EXPECTED_ADDR='+addresses['erf_cpu_expected'],'+TESTED_ADDR='+addresses['erf_cpu_tested'],'+FAILURE_ADDR='+addresses['erf_cpu_failure'],'+CYCLES_ADDR='+addresses['erf_cpu_cycles'],'+GOLDEN='+str(case/'golden.hex'),'+REPORT='+str(case/'report.txt'),'+MAX_CYCLES=15000000']
normal=run(argv,'normal');assert 'INSTALLED_NEWLIB_ERF_CPU_PASS tested=384' in normal and 'ERF_CPU_RETURN_PASS' in normal and 'Fatal:' not in normal
negative=run([*argv,'+INJECT_REFERENCE_ERROR'],'negative',True);assert 'ERF_CPU_NEGATIVE_PASS' in negative and 'ERF_CPU_RETURN_FAIL code=bad00061' in negative and 'ERF_CPU_RETURN_PASS'not in negative
assert hashlib.sha256((case/'simv').read_bytes()).hexdigest()==digest
summary={'status':'PASS','cases':384,'scope':'actual installed RV32/newlib erff numeric bits vs archived classic source, NaN class matching; independent CPU output inspection and corrupted-reference negative control; vector/Softmax integration pending',
 'simulator_sha256':digest,'program_sha256':hashlib.sha256((case/'program.elf').read_bytes()).hexdigest(),'report':(case/'report.txt').read_text(),'commands':commands,
 'source_sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest()for p in [ROOT/'c/erf_cpu_main.c',case/'erf_cpu_vectors.h',ROOT/'tests/erf_cpu_monitor.sv',ROOT/'work/erf-reference/vectors.hex']}}
(case/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print('INSTALLED_NEWLIB_ERF_VERIFIED',flush=True)
