"""Collect the surviving original-RTL VCS run without restarting it.

Never fabricate the orphan process exit code. Review immutable archives,
actual runtime and complete log/report/logits after the identified PID exits.
"""
import argparse, hashlib, json, math, os, re, struct, sys, time
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parent;BASE=ROOT.parents[1]
p=argparse.ArgumentParser();p.add_argument('--revision',default='v1');p.add_argument('--inspect-only',action='store_true');args=p.parse_args()
assert re.fullmatch(r'[A-Za-z0-9_-]+',args.revision)
CASE=ROOT/'work/original-baseline-full-vcs/v1';TARGET=ROOT/('work/original-vcs-orphan-collection-'+args.revision)
PROJECT=BASE/'native/audits/20261107/riscv_pe_contest/contest/task1/my_design/project'
sys.path.insert(0,str(PROJECT/'scripts'))
from run_pe_axi_sdf import compiled_runtime_files

def local(s):
    for prefix in ['/workspace/','/mnt/data/Ubuntu/qingruan/']:
        if str(s).startswith(prefix):return BASE/str(s)[len(prefix):]
    return Path(s)
def sha(p):
    with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def verify(h):
    for n,s in h.items():assert sha(local(n))==s,n
def save(name,d):
    t=TARGET/(name+'.tmp');t.write_text(json.dumps(d,indent=2)+'\n');t.replace(TARGET/name)
def proc(pid):
    try:
        s=Path('/proc/%d/stat'%pid).read_text().rsplit(')',1)[1].split()
        if s[0]=='Z':return None
        return {'pid':pid,'start_ticks':int(s[19]),'argv':Path('/proc/%d/cmdline'%pid).read_bytes().decode().rstrip('\0').split('\0')}
    except (FileNotFoundError,ProcessLookupError):return None
prep=json.loads((CASE/'preparation.json').read_text());live=json.loads((CASE/'live-process.json').read_text())
assert not (CASE/'summary.json').exists() and not (CASE/'failure.json').exists(),'Original owner already collected result'
verify(prep['source_sha256']);template=local(prep['template'])
assert compiled_runtime_files(template)==prep['runtime_files']
identity=proc(live['pid'])
if identity:assert identity['argv']==live['argv']==prep['argv'],'PID/argv changed'
if args.inspect_only:print(json.dumps({'identity':identity,'archive_and_runtime_verified':True,'would_restart':False},indent=2));raise SystemExit(0)
assert not TARGET.exists(),'Preserve prior collection; no duplicate collector'
TARGET.mkdir()
guards={str(Path(__file__)):sha(Path(__file__)),str(CASE/'preparation.json'):sha(CASE/'preparation.json')}
save('preparation.json',{'status':'ORIGINAL_ORPHAN_RESULT_COLLECTOR_WAITING','identity':identity,'source_sha256':guards,'process_exit_status_unavailable':True,'no_simulator_started':True})
while identity:
    current=proc(identity['pid'])
    if not current or current['start_ticks']!=identity['start_ticks']:break
    assert current['argv']==identity['argv'],'Live process identity changed'
    save('progress.json',{'status':'WAITING_FOR_IDENTIFIED_ORIGINAL_VCS_NO_RESTART','updated_at':datetime.now(timezone.utc).isoformat(),'pid':identity['pid'],'start_ticks':identity['start_ticks'],'process_returncode':None})
    time.sleep(30)
verify(prep['source_sha256']);verify(guards);assert compiled_runtime_files(template)==prep['runtime_files']
log=(CASE/'normal.log').read_text(errors='replace');report=(CASE/'report.txt').read_text() if (CASE/'report.txt').exists() else ''
rows=re.findall(r'^RESULT (.+)$',report,re.M)
m={k:int(v,16 if k in ['code','max_error_bits'] else 10) for k,v in re.findall(r'(\w+)=([0-9a-f]+)',rows[0])} if len(rows)==1 else {}
raw=(CASE/'logits.bin').read_bytes() if (CASE/'logits.bin').exists() else b''
expected=[local(n) for n in prep['source_sha256'] if n.endswith('ILSVRC2012_val_00000274.JPEG.baseline')]
assert len(expected)==1
finite=len(raw)==4000 and all(math.isfinite(x) for x in struct.unpack('<1000f',raw))
exact=finite and raw==expected[0].read_bytes()
nodes=re.findall(r'^ORIGINAL_BASELINE_NODE_DONE node=(\d+) cycles=(\d+) CPU_cycle=(\d+)$',log,re.M)
counts=(m.get('PE_AW')==m.get('PE_W')==m.get('PE_B')==181690705 and m.get('PE_AR')==m.get('PE_R')==1148252160)
passed=bool(log.count('ORIGINAL_BASELINE_FULL_MODEL_PASS nodes=1396 MAC_jobs=77')==log.count('ORIGINAL_BASELINE_CPU_RETURN_PASS')==1 and '$finish called from file' in log and 'V C S   S i m u l a t i o n   R e p o r t' in log and not re.search(r'Fatal:|Error-\[|ORIGINAL_BASELINE_FAIL|AXI_RX_(?:SETUP_FAIL|UNKNOWN)',log) and len(nodes)==1396 and len({n[0] for n in nodes})==1396 and nodes[-1][0]=='1395' and m.get('code')==0x600dcafe and m.get('node_events')==1396 and m.get('error_node')==m.get('logit_mismatched_words')==m.get('max_error_bits')==0 and m.get('CPU_return_cycle',0)>m.get('inference_cycles',0)>0 and counts and exact)
result={'status':'RECOVERED_ORIGINAL_FULL_CPU_PE_LOG_REPORT_LOGITS_REVIEW_PASS_EXIT_STATUS_UNAVAILABLE' if passed else 'RECOVERED_ORIGINAL_FULL_VCS_DIAGNOSTIC_NOT_ACCEPTED','full_log_report_logits_review_pass':passed,'process_returncode':None,'process_returncode_observed':False,'measured':m,'all1000_logits_finite':finite,'logits_byte_exact_to_frozen_C':exact,'source_archive_verified':True,'runtime_files_unchanged':True,'source_sha256':{**prep['source_sha256'],**guards},'normal_log_sha256':sha(CASE/'normal.log'),'report_sha256':sha(CASE/'report.txt') if report else None,'official_accuracy_accepted':False,'final_ADP_accepted':False,'scope':'Existing original raw-RTL full run only. Adopted exit status unavailable, never invented. No rerun, no official accuracy acceptance.'}
save('summary.json' if passed else 'failure.json',result)
print(result['status'],flush=True)
