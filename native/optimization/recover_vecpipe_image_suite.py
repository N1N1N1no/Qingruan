"""Resume only the unstarted queue and collect surviving orphan VCS jobs.

Original sources, runtime, completed results and old progress remain unchanged.
An adopted process has no recoverable wait() status: record this explicitly and
review its complete simulator finish/log/report/output independently.
"""
import argparse, ast, fcntl, hashlib, json, math, os, re, resource, signal, struct
import subprocess, sys, time
from pathlib import Path
from datetime import datetime, timezone
ROOT = Path(__file__).resolve().parent
BASE = ROOT.parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--revision', default='v1')
parser.add_argument('--inspect-only', action='store_true')
args = parser.parse_args()
assert re.fullmatch(r'[A-Za-z0-9_-]+', args.revision)
case = ROOT / 'work/vecpipe-images-rv32-v1'
TARGET = ROOT / ('work/vecpipe-suite-recovery-' + args.revision)
PROJECT = BASE / 'native/audits/20261107/riscv_pe_contest/contest/task1/my_design/project'
sys.path.insert(0, str(PROJECT / 'scripts'))
from run_pe_axi_sdf import compiled_runtime_files

def local(name):
    for prefix in ['/workspace/', '/mnt/data/Ubuntu/qingruan/']:
        if str(name).startswith(prefix): return BASE / str(name)[len(prefix):]
    return Path(name)

def sha(path):
    with Path(path).open('rb') as f: return hashlib.file_digest(f, 'sha256').hexdigest()

def verify(hashes):
    for name, expected in hashes.items(): assert sha(local(name)) == expected, name

def save(path, data):
    t=path.with_name(path.name+'.tmp');t.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n');t.replace(path)

def proc(pid):
    try:
        fields=Path('/proc/%d/stat'%pid).read_text().rsplit(')',1)[1].split()
        if fields[0]=='Z': return None
        argv=Path('/proc/%d/cmdline'%pid).read_bytes().decode().rstrip('\0').split('\0')
        return {'pid':pid,'start_ticks':int(fields[19]),'argv':argv}
    except (FileNotFoundError,ProcessLookupError): return None

def same_argv(a,b):
    return [str(local(x)) if x.startswith('/') else x for x in a]==[str(local(x)) if x.startswith('/') else x for x in b]

# Refuse a second controller, including a prior recovery after reconnect.
for d in Path('/proc').iterdir():
    if not d.name.isdigit() or int(d.name)==os.getpid(): continue
    p=proc(int(d.name))
    if not p: continue
    a=p['argv']
    if a and Path(a[0]).name.startswith('python'):
        scripts=[Path(x).name for x in a[1:3] if not x.startswith('-')]
        assert 'run_image_suite.py' not in scripts and Path(__file__).name not in scripts, ('controller already alive',p['pid'])
master_prep=json.loads((case/'preparation.json').read_text())
master_sources=master_prep['source_sha256'];verify(master_sources)
runtime_case=case/'runtime'
runtime_proof=json.loads((runtime_case/'compile-summary.json').read_text())
runtime_files=runtime_proof['runtime_files']
assert compiled_runtime_files(runtime_case)==runtime_files
executable=runtime_case/'simv'
primary=ROOT/'work/vecpipe-full-rv32-v1'
prep=json.loads((primary/'preparation.json').read_text())
suite=json.loads((ROOT/'work/vecpipe-image-suite-v1/summary.json').read_text())
records={x['image']:x for x in suite['images'] if x['image']!=prep['image']}
assert len(records)==19
active={};completed=[];pending=[]
for name in master_prep['execution_order']:
    directory=case/name
    if (directory/'summary.json').exists() or (directory/'failure.json').exists():
        p=directory/('summary.json' if (directory/'summary.json').exists() else 'failure.json')
        r=json.loads(p.read_text());verify(r['source_sha256']);assert sha(directory/'normal.log')==r['normal_log_sha256']
        completed.append({'image':name,'result':str(p),'status':r['status'],'byte_exact':r['logits_byte_exact_to_frozen_C'],'complete_graph':r['complete_graph_execution_observed'],'returncode':r['command']['returncode']})
    elif directory.exists():
        pr=json.loads((directory/'preparation.json').read_text());verify(pr['source_sha256'])
        live=json.loads((directory/'live-process.json').read_text());p=proc(live['pid'])
        if p: assert same_argv(p['argv'],pr['argv']), name
        active[name]={'directory':directory,'item':records[name],'adopted':True,'identity':p,'pid':live['pid'],'argv':pr['argv'],'hashes':pr['source_sha256'],'started':time.monotonic(),'process':None,'stream':None,'timed_out':False}
    else: pending.append(records[name])
if args.inspect_only:
    print(json.dumps({'completed':len(completed),'surviving_or_uncollected':list(active),'unstarted':[x['image'] for x in pending],'runtime_guard_verified':True},indent=2));raise SystemExit(0)
TARGET.mkdir(exist_ok=True)
lock=(TARGET/'controller.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
assert not (TARGET/'preparation.json').exists(), 'Preserve prior recovery; use a fresh revision after confirming its controller exited'
guards={str(Path(__file__)):sha(Path(__file__)),str(case/'preparation.json'):sha(case/'preparation.json'),str(runtime_case/'compile-summary.json'):sha(runtime_case/'compile-summary.json')}
original=ROOT/'variants/vecpipe_v1/run_image_suite.py'
guards[str(original)]=sha(original)
save(TARGET/'preparation.json',{'status':'ORPHAN_SUITE_RECOVERY_STARTED_NO_SIMULATOR_RESTART','observed_at':datetime.now(timezone.utc).isoformat(),'controller':proc(os.getpid()),'adopted':{k:v['identity'] for k,v in active.items()},'pending':[x['image'] for x in pending],'source_sha256':guards,'original_sources_unchanged':True,'adopted_exit_status_recoverable':False})
# Reuse the frozen start/limit/headroom implementations without executing the
# original module's compile/top-level launch. Guard their exact source hash.
tree=ast.parse(original.read_text())
selected=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ['limits','resource_headroom','start_image']]
assert len(selected)==3
exec(compile(ast.Module(body=selected,type_ignores=[]),str(original),'exec'),globals())

def finish(name, job):
    directory=job['directory'];item=job['item'];verify(job['hashes']);verify(master_sources);verify(guards)
    assert compiled_runtime_files(runtime_case)==runtime_files
    if job['stream']: job['stream'].close()
    text=(directory/'normal.log').read_text(errors='replace')
    report=(directory/'report.txt').read_text() if (directory/'report.txt').exists() else ''
    result_lines=re.findall(r'^RESULT (.+)$',report,re.M)
    measured={k:int(v,16 if k in ['code','max_error_bits'] else 10) for k,v in re.findall(r'(\w+)=([0-9a-f]+)',result_lines[0])} if len(result_lines)==1 else {}
    raw=(directory/'logits.bin').read_bytes() if (directory/'logits.bin').exists() else b''
    values=struct.unpack('<1000f',raw) if len(raw)==4000 else ()
    finite=len(values)==1000 and all(math.isfinite(v) for v in values)
    exact=finite and hashlib.sha256(raw).hexdigest()==item['frozen_C_logits_sha256']
    nodes=re.findall(r'^FULL_MODEL_NODE_DONE node=(\d+) cycles=(\d+) CPU_cycle=(\d+)$',text,re.M)
    engines=re.findall(r'^ENGINE (\d+) commands=(\d+) cycles=(\d+) written_bytes=(\d+)$',report,re.M)
    graph=(len(nodes)==1312 and len({x[0] for x in nodes})==1312 and nodes[-1][0]=='1395' and measured.get('requested')==measured.get('completed')==4640 and measured.get('node_events')==1312)
    log_pass=(text.count('FULL_MODEL_CPU_RETURN_PASS')==text.count('FULL_MODEL_RV32_PASS')==1 and '$finish called from file' in text and 'V C S   S i m u l a t i o n   R e p o r t' in text and not re.search(r'Fatal:|Error-\[|FULL_MODEL_FAIL|AXI_RX_(?:SETUP_FAIL|UNKNOWN)|VECPIPE_FIFO_FAIL',text))
    code=job['process'].returncode if job['process'] else None
    all8=len(engines)==8 and {int(x[0]) for x in engines}==set(range(1,9)) and all(min(map(int,x[1:]))>0 for x in engines) and sum(int(x[1]) for x in engines)==4640
    functional=bool(graph and all8 and log_pass and finite and exact and measured.get('code')==0x600dcafe and measured.get('max_error_bits')==measured.get('logit_mismatched_words')==measured.get('error_node')==0 and not job.get('timed_out') and (code in [None,0]))
    status=('RECOVERED_COMPLETE_GRAPH_FROZEN_C_BYTE_EXACT_LOG_REVIEW_PASS_EXIT_STATUS_UNAVAILABLE' if job['adopted'] else 'ACTUAL_RV32_FULL_IMAGE_FROZEN_C_BYTE_EXACT_PASS_OFFICIAL_REFERENCE_OPEN') if functional else 'ACTUAL_RV32_FULL_IMAGE_DIAGNOSTIC_NOT_ACCEPTED'
    top=max(range(1000),key=values.__getitem__) if finite else None
    result={'status':status,'image':name,'actual_RV32_execution_observed':bool(nodes),'complete_graph_execution_observed':graph,'all8_engine_counters_checked':all8,'log_complete_functional_pass':functional,'logits_byte_exact_to_frozen_C':exact,'logits_sha256':hashlib.sha256(raw).hexdigest() if raw else None,'all1000_logits_finite':finite,'actual_top1':top,'public_reference_top1':item['public_reference_top1'],'public_reference_top1_matches':top==item['public_reference_top1'] if item['public_reference_top1'] is not None else None,'measured':measured,'adopted_process':job['adopted'],'process_returncode':code,'process_returncode_observed':code is not None,'normal_log_sha256':sha(directory/'normal.log'),'report_sha256':sha(directory/'report.txt') if report else None,'source_sha256':job['hashes'],'controller_source_sha256':guards,'runtime_files':runtime_files,'official_accuracy_accepted':False,'final_ADP_accepted':False,'scope':'Complete graph/log/report/output review. Orphan process wait status is unavailable and never fabricated; all old diagnostics retained unchanged.'}
    out=TARGET/(name+'.json');save(out,result)
    completed.append({'image':name,'result':str(out),'status':status,'byte_exact':exact,'complete_graph':graph,'returncode':code})
    print('RECOVERY_COLLECTED',name,status,flush=True)

while pending or active:
    for name,job in list(active.items()):
        if 'adopted' not in job: job['adopted']=False
        if job['adopted']:
            current=proc(job['pid'])
            alive=bool(current and job['identity'] and current['start_ticks']==job['identity']['start_ticks'] and same_argv(current['argv'],job['argv']))
        else:
            alive=job['process'].poll() is None
            if alive and time.monotonic()-job['started']>86400:
                os.killpg(job['process'].pid,signal.SIGTERM);job['process'].wait(timeout=10);job['timed_out']=True;alive=False
        if not alive: finish(name,job);del active[name]
    while pending and len(active)<4 and resource_headroom():
        item=pending.pop(0);start_image(item);active[item['image']]['adopted']=False
    save(TARGET/'progress.json',{'status':'RECOVERY_QUEUE_ACTIVE','updated_at':datetime.now(timezone.utc).isoformat(),'controller_pid':os.getpid(),'active':[{'image':n,'pid':j['identity']['pid'] if j['adopted'] and j['identity'] else j['process'].pid if j['process'] else j['pid'],'adopted':j['adopted']} for n,j in active.items()],'pending':[x['image'] for x in pending],'completed':completed,'official_accuracy_accepted':False,'final_ADP_accepted':False})
    if pending or active: time.sleep(30)
verify(master_sources);verify(guards);assert len(completed)==19
save(TARGET/'summary.json',{'status':'RECOVERED_REMAINING19_ATTEMPTS_FINISHED_REVIEW_RESULTS','completed':completed,'source_sha256':guards,'official_accuracy_accepted':False,'final_ADP_accepted':False,'adopted_exit_status_not_fabricated':True})
print('RECOVERY_QUEUE_FINISHED',flush=True)
