"""Compare a resumed node prefix with the identical uninterrupted prefix.

The activation arena, command cycles and periodic RAM-stall phase must all
match. This check does not assert complete inference; the subsequent run does.
"""
import hashlib,json,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent
case_name='rtl-model-checkpoint-r8-v1'
case=ROOT/'work'/case_name;image='ILSVRC2012_val_00000274.JPEG';result=case/image
def run(extra,label):
 print('CHECKPOINT_TEST_PHASE='+label,flush=True)
 argv=[sys.executable,str(ROOT/'run_rtl_model.py'),'--case-name',case_name,'--image',image,*extra]
 subprocess.run(argv,check=True)
def metadata(name):return json.loads((result/(name+'.bin.json')).read_text())
run(['--checkpoint-name','resumed','--checkpoint-interval','1','--stop-after-checkpoint'],'first_boundary')
first=metadata('resumed');assert first['next_node']==386 and first['executed_jobs']==0
run(['--reuse-compile','--checkpoint-name','uninterrupted','--checkpoint-interval',str(first['engine_cycles']+1),'--stop-after-checkpoint'],'uninterrupted_prefix')
run(['--reuse-compile','--checkpoint-name','resumed','--checkpoint-interval','1','--stop-after-checkpoint'],'resumed_prefix')
a,b=metadata('uninterrupted'),metadata('resumed')
for key in ['next_node','fusion_index','executed_jobs','logical_tick','engine_cycles','MAC_cycles','GELU_cycles','quantizer_cycles','identity','arena_bytes']:
 assert a[key]==b[key],(key,a[key],b[key])
assert a['next_node']==443 and a['executed_jobs']==1 and b['segments']==2
aa=(result/'uninterrupted.bin').read_bytes()[a['header_bytes']:]
bb=(result/'resumed.bin').read_bytes()[b['header_bytes']:]
assert len(aa)==a['arena_bytes'] and aa==bb
summary={'status':'PASS','scope':'resumed first quantizer -> first complete MAC node matches uninterrupted prefix, full activation arena and cycle/stall phase; complete inference checked separately',
 'next_node':a['next_node'],'activation_bytes':len(aa),'activation_sha256':hashlib.sha256(aa).hexdigest(),'logical_tick':a['logical_tick'],'engine_cycles':a['engine_cycles']}
(case/'checkpoint-test-summary.json').write_text(json.dumps(summary,indent=2)+'\n');print('MODEL_CHECKPOINT_PREFIX_EQUIVALENCE_PASS',flush=True)
