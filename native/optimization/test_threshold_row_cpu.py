"""Actual installed RV32 Softmax for the captured quantization-threshold row."""
import hashlib,json,re,struct,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parent;BASE=ROOT.parents[1]
TASK=BASE/'native/audits/20261107/riscv_pe_contest/contest/task1';PROJECT=TASK/'my_design/project'
case=ROOT/'work/threshold-cpu';case.mkdir(parents=True,exist_ok=True)
capture=ROOT/'work/newlib-difference';plan=ROOT/'work/fused-plan'
raw=(capture/'threshold-row-input.bin').read_bytes();expected=(capture/'threshold-row-newlib.bin').read_bytes();glibc=(capture/'threshold-row-glibc.bin').read_bytes()
assert len(raw)==len(expected)==len(glibc)==196 and expected!=glibc
graph=(plan/'graph.bin').read_bytes();h=struct.unpack_from('<16I',graph);node=struct.unpack_from('<4I8i',graph,h[8]+558*132);assert node[0]==16
constants=(plan/'constants.bin').read_bytes()
ts=struct.unpack_from('<6I',graph,h[7]+node[5]*56);tz=struct.unpack_from('<6I',graph,h[7]+node[6]*56)
assert ts[1:5]==(1,1,0,0)and tz[1:5]==(1,3,0,0)
scale=struct.unpack_from('<f',constants,ts[0])[0];zero=struct.unpack_from('<b',constants,tz[0])[0]
header='#define THRESHOLD_SCALE '+float(scale).hex()+'f\n#define THRESHOLD_ZERO '+str(zero)+'\n'
for name,data in [('threshold_input',raw),('threshold_expected',expected)]:header+='const uint32_t '+name+'[49]={'+','.join(hex(x)+'u'for x in struct.unpack('<49I',data))+'};\n'
(case/'threshold_row_vectors.h').write_text(header);(case/'golden.hex').write_text(''.join('%08x\n'%x for x in struct.unpack('<49I',expected)))
ram=(PROJECT/'tb/axi_ram_model.sv').read_text().replace('SOC_SMOKE_PASS: CPU checked all 64 PE output words','THRESHOLD_CPU_RETURN_PASS').replace('SOC_SMOKE_FAIL','THRESHOLD_CPU_RETURN_FAIL')
ram=ram.replace('else $fatal(1,"THRESHOLD_CPU_RETURN_FAIL code=%08x",done_value);','else begin #0; $fatal(1,"THRESHOLD_CPU_RETURN_FAIL code=%08x",done_value); end');(case/'axi_ram_model.sv').write_text(ram)
commands=[]
def run(argv,label,allow_failure=False):
 print('THRESHOLD_CPU_PHASE='+label,flush=True);argv=list(map(str,argv))
 with(case/(label+'.log')).open('w')as log:r=subprocess.run(argv,cwd=case,stdout=log,stderr=subprocess.STDOUT,timeout=600)
 commands.append({'label':label,'argv':argv,'returncode':r.returncode})
 if r.returncode and not allow_failure:raise RuntimeError(str(case/(label+'.log')))
 return(case/(label+'.log')).read_text(errors='replace')
prefix='/home/zhz/rtl_tools/xpack-riscv-none-elf-gcc-14.2.0-3/bin/riscv-none-elf-'
run([prefix+'gcc','-march=rv32imc_zicsr','-mabi=ilp32','-O2','-ffreestanding','-fno-builtin','-ffp-contract=off','-msmall-data-limit=0','-nostartfiles','-Wl,--no-relax','-Wl,-T,'+str(PROJECT/'sw/link.ld'),'-I'+str(ROOT/'c'),'-I'+str(case),PROJECT/'sw/start.S',ROOT/'c/threshold_row_main.c',ROOT/'c/accel_io.c','-lm','-lc','-lgcc','-o',case/'program.elf'],'firmware')
run([prefix+'objcopy','-O','binary',case/'program.elf',case/'program.bin'],'objcopy');(case/'program.hex').write_text(''.join('%02x\n'%v for v in(case/'program.bin').read_bytes()))
symbols=run([prefix+'nm','-S',case/'program.elf'],'symbols');addresses={}
for name,size in [('threshold_actual',196),('threshold_expected',196),('threshold_quant',196),('threshold_failure',4),('threshold_cycles',8)]:
 m=re.search(r'^([0-9a-f]+)\s+([0-9a-f]+)\s+[A-Za-z]\s+'+name+'$',symbols,re.M);assert m and int(m[2],16)==size;addresses[name]=m[1]
run(['/usr/bin/gcc','-fPIC','-c',TASK/'my_design/compat/pthread_yield_compat.c','-o',case/'compat.o'],'compat')
files=[ROOT/'c/threshold_row_main.c',ROOT/'c/accel_io.c',case/'threshold_row_vectors.h',ROOT/'tests/threshold_cpu_monitor.sv',capture/'threshold-row-input.bin',capture/'threshold-row-newlib.bin',capture/'threshold-row-glibc.bin']
hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest()for p in files}
run(['/home/zhz/miniforge3/bin/python3',TASK/'scripts/vcs_vm.py','-full64','-sverilog','-timescale=1ns/1ps','-debug_access','-assert','svaext','+define+VCS','+define+PE_REFERENCE_MACRO','+define+AXI_RX_TIMING_CHECK','+define+AXI_RAM_OUTPUT_DELAY_NS=1.0','-f',PROJECT/'build/rtl.f',PROJECT/'tb/sram_reference_model.v',case/'axi_ram_model.sv',PROJECT/'tb/tb_soc.sv',ROOT/'tests/threshold_cpu_monitor.sv','-top','tb_soc','-top','threshold_cpu_monitor','-Mdir='+str(case/'csrc'),'-o',case/'simv','-LDFLAGS',case/'compat.o'],'compile')
digest=hashlib.sha256((case/'simv').read_bytes()).hexdigest()
argv=[case/'simv','+MEM_HEX='+str(case/'program.hex'),'+MAX_CYCLES=5000000','+ACTUAL='+addresses['threshold_actual'],'+EXPECTED='+addresses['threshold_expected'],'+QUANT='+addresses['threshold_quant'],'+FAILURE='+addresses['threshold_failure'],'+CYCLES='+addresses['threshold_cycles'],'+GOLDEN='+str(case/'golden.hex'),'+REPORT='+str(case/'report.txt')]
normal=run(argv,'normal');assert'THRESHOLD_CPU_PASS values=49 first_quant=134'in normal and'THRESHOLD_CPU_RETURN_PASS'in normal and'Fatal:'not in normal
negative=run([*argv,'+CORRUPT_REFERENCE'],'negative',True);assert'THRESHOLD_CPU_NEGATIVE_PASS'in negative and'THRESHOLD_CPU_RETURN_FAIL code=bad000d1'in negative and'THRESHOLD_CPU_RETURN_PASS'not in negative
for p,want in hashes.items():assert hashlib.sha256(Path(p).read_bytes()).hexdigest()==want
assert hashlib.sha256((case/'simv').read_bytes()).hexdigest()==digest
summary={'status':'PASS','values':49,'captured_graph_node':557,'quantized_node':558,'row':7119,'first_RV32_quantized_raw':134,'first_glibc_quantized_raw':135,
 'source_sha256':hashes,'commands':commands,'simulator_sha256':digest,'report':(case/'report.txt').read_text(),
 'scope':'actual installed RV32 Softmax+Quantize on captured model input, exact classic-Newlib row bits; glibc distinction reproduced, corrupted-reference control detected; full RV32 model and official accuracy pending'}
(case/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print('MODEL_THRESHOLD_ROW_RV32_VERIFIED',flush=True)
