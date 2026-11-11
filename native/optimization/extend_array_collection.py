"""Preserve an identified live DC run past its original collector deadline.

The old collector alone is stopped while DC continues in its own process
group. This supervisor owns a longer bounded deadline, restores the collector
only after DC terminates, then harvests complete error-free artifacts.
"""
import argparse,json,os,re,signal,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent;BASE=ROOT.parents[1]
parser=argparse.ArgumentParser();parser.add_argument('--collector',type=int,required=True);parser.add_argument('--dc',type=int,required=True);parser.add_argument('--max-total-seconds',type=int,default=14400);args=parser.parse_args()
case=ROOT/'work/array-synthesis/R16_C16_L16';summary=case/'extended-collection.json'
def proc(pid):
 try:
  p=Path('/proc')/str(pid);raw=(p/'stat').read_text();fields=raw[raw.rfind(')')+2:].split()
  return {'state':fields[0],'ppid':int(fields[1]),'start_ticks':fields[19],'cmd':(p/'cmdline').read_bytes().replace(b'\0',b' ').decode()}
 except FileNotFoundError:return None
collector=proc(args.collector);dc=proc(args.dc)
assert collector and dc and dc['state']!='Z'and dc['ppid']==args.collector
assert'sweep_arrays.py synthesis --resume --memory-gib 12'in collector['cmd']
assert'common_shell_exec'in dc['cmd']and'R16_C16_L16/run.tcl'in dc['cmd']and os.getpgid(args.dc)==args.dc
assert 7200<args.max_total_seconds<=21600
age=int(subprocess.check_output(['ps','-p',str(args.dc),'-o','etimes='],text=True));deadline=time.monotonic()+args.max_total_seconds-age
assert deadline>time.monotonic()
record={'status':'RUNNING_EXTENDED_COLLECTION','collector_pid':args.collector,'DC_pid':args.dc,'collector_identity':collector,'DC_identity':dc,'DC_age_at_start_s':age,'max_DC_total_elapsed_s':args.max_total_seconds,
 'scope':'deadline extension only; same identified DC process, RTL, library, clock, Tcl and 12GiB address-space cap; no synthesis restart'}
def save():summary.write_text(json.dumps(record,indent=2)+'\n')
def same(pid,identity):
 current=proc(pid);return current if current and current['start_ticks']==identity['start_ticks']else None
save();os.kill(args.collector,signal.SIGSTOP);print('ARRAY_COLLECTOR_HELD_DC_CONTINUES pid='+str(args.dc),flush=True)
try:
 while True:
  current=same(args.dc,dc)
  if not current or current['state']=='Z':break
  if time.monotonic()>=deadline:
   record['status']='EXTENDED_DC_DEADLINE_EXPIRED';save();os.killpg(args.dc,signal.SIGTERM)
   for _ in range(10):
    current=same(args.dc,dc)
    if not current or current['state']=='Z':break
    time.sleep(1)
   else:os.killpg(args.dc,signal.SIGKILL)
   break
  print('ARRAY_DC_VERIFIED_LIVE_EXTENDED_COLLECTION pid='+str(args.dc),flush=True);time.sleep(30)
finally:
 if same(args.collector,collector):os.kill(args.collector,signal.SIGCONT)
for _ in range(60):
 current=same(args.collector,collector)
 if not current or current['state']=='Z':break
 time.sleep(1)
else:raise RuntimeError('old collector did not terminate after identified DC termination')
log=(case/'synthesis.log').read_text(errors='replace')
complete=all((case/n).is_file()and(case/n).stat().st_size>0 for n in['mapped.ddc','mapped.v','area.rpt','timing.rpt','violations.rpt'])
if record['status']=='EXTENDED_DC_DEADLINE_EXPIRED'or not complete or not re.search(r'^AFFINE_ARRAY_SYNTHESIS_PASS$',log,re.M)or'Thank you...'not in log or re.search(r'^Error:',log,re.M):
 record['status']='DC_NOT_COMPLETE_OR_FAILED_NO_AUTOMATIC_RESTART';save();raise RuntimeError(str(summary))
with(ROOT/'array-extended-harvest.log').open('w')as out:
 code=subprocess.call([str(BASE/'run-native.sh'),'/home/zhz/miniforge3/bin/python3','-B','/workspace/native/optimization/sweep_arrays.py','synthesis','--resume','--memory-gib','12'],cwd=BASE,stdout=out,stderr=subprocess.STDOUT)
record['status']='COMPLETE_ERROR_FREE_ARTIFACTS_HARVESTED'if code==0 else'HARVEST_COLLECTOR_FAILED';record['harvest_exit_code']=code;save();assert code==0
print('ARRAY_EXTENDED_COLLECTION_COMPLETE',flush=True)
