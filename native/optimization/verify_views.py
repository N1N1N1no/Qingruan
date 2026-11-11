import importlib.util,json,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parent;BASE=ROOT.parents[1]
source=BASE/'native/swin-tiny/work/verify.ylnpqxth';views=ROOT/'work/view-plan';table=ROOT/'work/lookup-tables';fused=ROOT/'work/fusion-proof'
case=ROOT/'work/view-proof';case.mkdir(parents=True,exist_ok=True)
spec=importlib.util.spec_from_file_location('unchanged_preprocess',BASE/'competition/official_preprocess.py');pre=importlib.util.module_from_spec(spec);spec.loader.exec_module(pre)
items=[]
for image in sorted((BASE/'competition/images').glob('*.JPEG')):
 inp=fused/(image.name+'.input');baseline=fused/(image.name+'.baseline');assert inp.is_file() and baseline.is_file()
 for label,extras in [('views',[]),('views_and_gelu',[table/'gelu.bin',table/'gelu-fusions.bin'])]:
  print('VIEW_PROOF '+image.name+' '+label,flush=True);out=case/(image.name+'.'+label)
  with (case/(image.name+'.'+label+'.log')).open('w') as f:
   r=subprocess.run(list(map(str,[fused/'host_opt',views/'graph.bin',views/'constants.bin',inp,out,*extras])),stdout=f,stderr=subprocess.STDOUT,timeout=120)
  assert not r.returncode and out.read_bytes()==baseline.read_bytes(),(image.name,label)
 items.append({'image':image.name,'views_bit_exact':True,'views_and_GELU_bit_exact':True})
summary={'status':'PASS','images':len(items),'results':items,'view_plan':json.loads((views/'summary.json').read_text()),
         'scope':'all20 complete C logits exactly equal to frozen C baseline, including aliases and GELU fusion; full hardware/official accuracy not asserted'}
assert len(items)==20
(case/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print('VIEWS_ALL20_BIT_EXACT_PASS',flush=True)
