"""Compare an actual CPU+RTL micrograph with recorded VCS output and cycles."""
import hashlib,json,os,re,resource,shutil,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent;BASE=ROOT.parents[1]
gold=ROOT/'work/complete-soc-R8_C16_L4/shared';proof=json.loads((gold/'summary.json').read_text());assert proof['status']=='PASS'
case=ROOT/'work/verilator-crosscheck';case.mkdir(parents=True,exist_ok=True)
assert not(case/'summary.json').exists(),'preserve completed proof'
def local(n):return BASE/n[11:]if n.startswith('/workspace/')else Path(n)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
for name,want in proof['source_sha256'].items():assert sha(local(name))==want,name
for name,want in proof['fixture_sha256'].items():assert sha(gold/name)==want,name
package=ROOT/'toolchain-cache/verilator-5.032/usr';tool=package/'bin/verilator';env=dict(os.environ,VERILATOR_ROOT=str(package/'share/verilator'))
source_argv=next(x['argv']for x in proof['commands']if x['label']=='compile');file_index=source_argv.index('-f');cpu_list=local(source_argv[file_index+1]);lines=[];sources=[]
for line in cpu_list.read_text().splitlines():
 if line.startswith('/'):p=local(line);lines.append(str(p));sources.append(p)
 elif line.startswith('+incdir+'):lines.append('+incdir+'+str(local(line[8:])))
 else:lines.append(line)
(case/'rtl.f').write_text('\n'.join(lines)+'\n')
extra=[]
for argument in source_argv[file_index+2:]:
 if argument.endswith(('.sv','.v')):
  p=local(argument)
  if p.parent==gold:
   target=case/p.name;data=p.read_text()
   if p.name=='axi_ram_model.sv':data=data.replace('#0;','#0.001;')
   target.write_text(data);extra.append(target)
  elif p.name=='divSqrtRecFN_small.v':
   # An ANSI output defaults to a net. Verilator rejects the redundant
   # wire redeclaration accepted by VCS; remove it only in an isolated copy.
   data=p.read_text();needle='    wire sqrtOpOut;';assert data.count(needle)==1 and 'output sqrtOpOut,'in data
   target=case/p.name;target.write_text(data.replace(needle,''));extra.append(target)
  else:extra.append(p)
assert any(p.name=='monitor.sv'for p in extra)
wrapper=case/'crosscheck_top.sv';wrapper.write_text('module crosscheck_top; tb_soc tb_soc(); complete_soc_monitor complete_soc_monitor(); endmodule\n')
frozen=[Path(__file__),gold/'summary.json',ROOT/'toolchain-cache/verilator-provenance.json',tool,package/'bin/verilator_bin',case/'rtl.f',wrapper,cpu_list,*sources,*extra,*map(local,proof['source_sha256'])]
hashes={str(p):sha(p)for p in dict.fromkeys(frozen)}
records=[]
def run(argv,label,timeout):
 print('VERILATOR_CROSSCHECK_PHASE '+label,flush=True);started=time.monotonic()
 with(case/(label+'.log')).open('w')as stream:
  def limits():resource.setrlimit(resource.RLIMIT_AS,(8*1024**3,8*1024**3))
  process=subprocess.Popen(list(map(str,argv)),cwd=case,stdout=stream,stderr=subprocess.STDOUT,env=env,preexec_fn=limits)
  while True:
   try:code=process.wait(timeout=30);break
   except subprocess.TimeoutExpired:
    print('VERILATOR_CROSSCHECK_RUNNING '+label,flush=True)
    if time.monotonic()-started>timeout:process.terminate();process.wait(timeout=20);raise TimeoutError(label)
 records.append({'phase':label,'returncode':code,'elapsed_s':time.monotonic()-started,'argv':list(map(str,argv))})
 if code:raise RuntimeError(str(case/(label+'.log')))
 return(case/(label+'.log')).read_text(errors='replace')
hf=ROOT/'vendor/HardFloat-1/source'
run([tool,'--binary','--timing','--assert','-Wno-fatal','--build-jobs','2','--top-module','crosscheck_top','--Mdir',case/'obj_dir','-o','crosscheck_simv','+define+PE_REFERENCE_MACRO','+define+AXI_RX_TIMING_CHECK','+define+AXI_RAM_OUTPUT_DELAY_NS=1.0','+incdir+'+str(hf),'+incdir+'+str(hf/'RISCV'),'-f',case/'rtl.f',*extra,wrapper],'compile',1800)
executable=case/'obj_dir/crosscheck_simv';exe_hash=sha(executable)
normal_argv=next(x['argv']for x in proof['commands']if x['label']=='normal');argv=[str(executable)]
for argument in normal_argv[1:]:
 if argument.startswith('+REPORT='):argv.append('+REPORT='+str(case/'report.txt'))
 elif argument.startswith('+MEM_HEX='):argv.append('+MEM_HEX='+str(local(argument.split('=',1)[1])))
 else:argv.append(argument)
normal=run(argv,'normal',1800)
assert 'COMPLETE_SOC_PASS commands=22'in normal and'COMPLETE_SOC_CPU_RETURN_PASS'in normal
assert not re.search(r'Fatal:|Error-\[|AXI_RX_(?:SETUP_FAIL|UNKNOWN)',normal)
trace=[list(map(int,m.groups()))for m in re.finditer(r'COMPLETE_SOC_ENGINE_DONE command=(\d+) cycles=(\d+) bytes=(\d+) error=(\d+)',normal)]
assert trace==proof['engine_trace'],'engine cycles differ from VCS'
assert(case/'report.txt').read_bytes()==(gold/'report.txt').read_bytes(),'CPU or call cycles differ from VCS'
for name,want in hashes.items():assert sha(Path(name))==want,name
assert sha(executable)==exe_hash
result={'status':'REAL_CPU_RTL_OUTPUT_AND_CYCLE_CROSSCHECK_PASS','commands':22,'geometry':'R8_C16_L4/shared','engine_trace_matches_VCS':True,'CPU_and_call_cycles_match_VCS':True,'source_sha256':hashes,'simulator_sha256':exe_hash,'commands_run':records,'compatibility_adapters':['isolated RAM model #0 completion delta changed to 1ps; same 10ns clock and 1ns AXI response timing; positive path only','isolated HardFloat divSqrtRecFN_small ANSI output redundant wire declaration removed; original arithmetic and vendor sources unchanged'],'scope':'actual CPU and all8 RTL functional/cycle crosscheck against VCS positive micrograph. No host arithmetic substitution, image inference, synthesis, gate/SDF or official accuracy claim.'}
(case/'summary.json').write_text(json.dumps(result,indent=2)+'\n');print('VERILATOR_CPU_RTL_VCS_CROSSCHECK_PASS',flush=True)
