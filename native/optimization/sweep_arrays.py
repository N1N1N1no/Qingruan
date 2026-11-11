"""Compare the same banked/pipelined native-affine fabric across configurations."""
import argparse,json,re,subprocess,time,hashlib,resource,os,signal
from pathlib import Path
ROOT=Path(__file__).resolve().parent;BASE=ROOT.parents[1]
TASK=BASE/'native/audits/20261107/riscv_pe_contest/contest/task1';PROJECT=TASK/'my_design/project'
parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['functional','synthesis']);parser.add_argument('--resume',action='store_true');parser.add_argument('--memory-gib',type=int,default=8,choices=range(8,17));args=parser.parse_args()
work=ROOT/'work'/('array-'+args.mode);work.mkdir(parents=True,exist_ok=True)
configs=[(8,32,4),(16,16,4),(8,32,8),(16,16,8),(16,32,4),(32,16,4),(8,16,4),(16,8,4),(4,64,4),(16,16,16)]
results=[]
if args.resume:
 proof=json.loads((ROOT/'progress.json').read_text())
 for rel in ['native/optimization/rtl/affine_array.v','native/optimization/tests/tb_affine_array.sv']:
  assert hashlib.sha256((BASE/rel).read_bytes()).hexdigest()==proof['source_sha256'][rel],rel
 if (work/'summary.json').is_file():results=json.loads((work/'summary.json').read_text())
 for r in results:
  p=work/r['id'];log=p/('synthesis.log' if args.mode=='synthesis' else 'simulate.log')
  assert r['status']=='PASS' and log.is_file()
  marker='AFFINE_ARRAY_SYNTHESIS_PASS' if args.mode=='synthesis' else 'AFFINE_ARRAY_PASS'
  assert marker in log.read_text(errors='replace'),str(log)
  if args.mode=='synthesis':
   assert not re.search(r'^Error:',log.read_text(errors='replace'),re.M),str(log)
   assert (p/'mapped.ddc').is_file() and (p/'mapped.ddc').stat().st_size>0
   assert abs(float(re.search(r'Total cell area:\s+([\d.]+)',(p/'area.rpt').read_text())[1])-r['area'])<0.01
def limit_memory():resource.setrlimit(resource.RLIMIT_AS,(args.memory_gib*1024**3,args.memory_gib*1024**3))
def run(argv,case,name):
 print('ARRAY_'+args.mode.upper()+' '+case.name+' '+name,flush=True)
 with (case/(name+'.log')).open('w') as f:
  p=subprocess.Popen(list(map(str,argv)),cwd=case,stdout=f,stderr=subprocess.STDOUT,start_new_session=True,preexec_fn=limit_memory if args.mode=='synthesis' else None)
  try:
   deadline=time.monotonic()+(7200 if args.mode=='synthesis' else 900)
   while True:
    remaining=deadline-time.monotonic()
    if remaining<=0:raise TimeoutError(str(case/(name+'.log')))
    try:code=p.wait(timeout=min(30,remaining));break
    except subprocess.TimeoutExpired:print('ARRAY_RUNNING '+case.name+' log='+str(case/(name+'.log')),flush=True)
  finally:
   if p.poll() is None:
    try:os.killpg(p.pid,signal.SIGTERM)
    except ProcessLookupError:pass
    try:p.wait(timeout=10)
    except subprocess.TimeoutExpired:
     os.killpg(p.pid,signal.SIGKILL);p.wait(timeout=10)
 if code:raise RuntimeError(str(case/(name+'.log')))
 return (case/(name+'.log')).read_text(errors='replace')
for rows,cols,lanes in configs:
 name=f'R{rows}_C{cols}_L{lanes}';case=work/name;case.mkdir(exist_ok=True);started=time.monotonic()
 if any(r['id']==name for r in results):
  print('ARRAY_RESUME_VERIFIED_SKIP '+name,flush=True);continue
 if args.resume and args.mode=='synthesis' and (case/'synthesis.log').is_file():
  # A shell interruption can kill the collector after DC has written all
  # reports. Harvest only complete artifacts rather than rerunning synthesis.
  old_log=(case/'synthesis.log').read_text(errors='replace')
  complete=all((case/p).is_file() and (case/p).stat().st_size>0 for p in ['mapped.ddc','mapped.v','area.rpt','timing.rpt','violations.rpt'])
  if complete and re.search(r'^AFFINE_ARRAY_SYNTHESIS_PASS$',old_log,re.M) and 'Thank you...' in old_log and not re.search(r'^Error:',old_log,re.M):
   area=(case/'area.rpt').read_text();timing=(case/'timing.rpt').read_text()
   cpu=re.search(r'CPU usage for this session ([\d.]+) seconds',old_log)
   result={'id':name,'status':'PASS','rows':rows,'columns':cols,'lanes':lanes,'SRAMs':rows*cols,
    'area':float(re.search(r'Total cell area:\s+([\d.]+)',area)[1]),
    'setup_slack_ns':float(re.search(r'slack\s+\([^)]+\)\s+(-?[\d.]+)',timing)[1]),'clock_period_ns':10,
    'elapsed_s':None,'synthesis_CPU_seconds':float(cpu[1]) if cpu else None,'recovered_after_collector_interruption':True,
    'artifacts_sha256':{p:hashlib.sha256((case/p).read_bytes()).hexdigest() for p in ['run.tcl','area.rpt','timing.rpt','violations.rpt','mapped.ddc']},
    'scope':'complete candidate array, independent bank access and reduction; full-SoC integration pending'}
   results.append(result);(work/'summary.json').write_text(json.dumps(results,indent=2)+'\n')
   print('ARRAY_RESUME_VERIFIED_HARVEST '+name,flush=True);continue
 if args.resume and (case/('synthesis.log' if args.mode=='synthesis' else 'simulate.log')).is_file():
  # The host process was verified absent before resuming. Retain partial evidence.
  backup=case.with_name(name+'.interrupted.'+time.strftime('%Y%m%dT%H%M%S'));case.rename(backup);case.mkdir()
 sources=[PROJECT/'rtl/rfsp16x8.v',ROOT/'rtl/affine_array.v']
 if args.mode=='functional':
  run(['/usr/bin/gcc','-fPIC','-c',TASK/'my_design/compat/pthread_yield_compat.c','-o',case/'compat.o'],case,'compat')
  run(['/home/zhz/miniforge3/bin/python3',TASK/'scripts/vcs_vm.py','-full64','-sverilog','-timescale=1ns/1ps','+define+PE_REFERENCE_MACRO',
       '+define+ARRAY_ROWS='+str(rows),'+define+ARRAY_COLS='+str(cols),'+define+ARRAY_LANES='+str(lanes),*sources,PROJECT/'tb/sram_reference_model.v',ROOT/'tests/tb_affine_array.sv',
       '-top','tb_affine_array','-Mdir='+str(case/'csrc'),'-o',case/'simv','-LDFLAGS',case/'compat.o'],case,'compile')
  text=run([case/'simv'],case,'simulate');assert f'AFFINE_ARRAY_PASS R={rows} C={cols} L={lanes} tiles=1000 outputs={cols*lanes*1000}' in text and 'Fatal:' not in text,text[-2000:]
  result={'id':name,'status':'PASS','rows':rows,'columns':cols,'lanes':lanes,'tiles':1000,'outputs':cols*lanes*1000,'pipeline_cycles':rows.bit_length()+1}
 else:
  std=PROJECT/'build/dc_libraries/standard.db';sram=PROJECT/'build/dc_libraries/sram.db'
  script='''set_app_var target_library [list {%s}]
set_app_var link_library [list * {%s} {%s}]
read_db {%s}
read_db {%s}
define_design_lib WORK -path {%s}
if {![analyze -library WORK -format sverilog -define {SYNTHESIS PE_REFERENCE_MACRO} [list %s]]} {error "array analysis failed"}
if {![elaborate affine_array -library WORK -parameters {ROWS=%d,COLS=%d,LANES=%d}]} {error "array elaboration failed"}
current_design [lindex [get_object_name [get_designs affine_array*]] 0]
if {![link]} {error "array link failed"}
create_clock -name clk -period 10 [get_ports clk]
set_input_delay 1 -clock clk [remove_from_collection [all_inputs] [get_ports {clk rst_n}]]
set_output_delay 1 -clock clk [all_outputs]
set_max_transition 0.5 [current_design]
if {![compile]} {error "array compile failed"}
if {[sizeof_collection [get_cells -hier -filter "ref_name == SRAM_6T_CORE_16x8_MC_TB"]] != %d} {error "array SRAM count"}
if {[sizeof_collection [get_cells -hier -filter "is_hierarchical == false && is_unmapped == true"]] != 0} {error "array unmapped leaves"}
redirect -file {%s} {report_area}
redirect -file {%s} {report_timing -max_paths 3}
redirect -file {%s} {report_constraint -all_violators}
write -format verilog -hierarchy -output {%s}
write -format ddc -hierarchy -output {%s}
puts AFFINE_ARRAY_SYNTHESIS_PASS
exit
'''%(std,std,sram,std,sram,case/'work',' '.join('{'+str(p)+'}' for p in sources),rows,cols,lanes,rows*cols,case/'area.rpt',case/'timing.rpt',case/'violations.rpt',case/'mapped.v',case/'mapped.ddc')
  script='if {[catch {\n'+script.replace('\nexit\n','\n')+'\n} array_error]} {puts stderr "ARRAY_SYNTHESIS_FAILED: $array_error"; exit 1}\nexit 0\n'
  (case/'run.tcl').write_text(script);text=run(['dc_shell','-f',case/'run.tcl'],case,'synthesis')
  assert re.search(r'^AFFINE_ARRAY_SYNTHESIS_PASS$',text,re.M) and not re.search(r'^Error:',text,re.M),str(case/'synthesis.log')
  area=(case/'area.rpt').read_text();timing=(case/'timing.rpt').read_text()
  result={'id':name,'status':'PASS','rows':rows,'columns':cols,'lanes':lanes,'SRAMs':rows*cols,
          'area':float(re.search(r'Total cell area:\s+([\d.]+)',area)[1]),
          'setup_slack_ns':float(re.search(r'slack\s+\([^)]+\)\s+(-?[\d.]+)',timing)[1]),'clock_period_ns':10,'synthesis_address_space_limit_gib':args.memory_gib}
 result.update(elapsed_s=time.monotonic()-started,scope='complete candidate array, independent bank access and reduction; full-SoC integration pending')
 results.append(result);(work/'summary.json').write_text(json.dumps(results,indent=2)+'\n')
print('ARRAY_'+args.mode.upper()+'_SWEEP_PASS',flush=True)
