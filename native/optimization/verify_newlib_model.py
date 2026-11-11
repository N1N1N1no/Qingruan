"""Measure the full graph effect of installed-Newlib expf vs frozen host C.

The host baseline uses glibc expf; matching the RV32 library must not be
silently reported as byte identity with a different host implementation.
"""
import hashlib,json,struct,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parent;BASE=ROOT.parents[1];SWIN=BASE/'native/swin-tiny'
case=ROOT/'work/newlib-model';case.mkdir(parents=True,exist_ok=True)
plan=ROOT/'work/fused-plan';baseline=ROOT/'work/fusion-proof';tables=ROOT/'work/lookup-tables'
wrapper=case/'wrap.c';wrapper.write_text('extern float reference_newlib_expf(float);\nfloat __wrap_expf(float x){return reference_newlib_expf(x);}\n')
files=[SWIN/'c/graph.c',SWIN/'c/pe_driver.c',ROOT/'c/host_opt.c',ROOT/'work/exp-reference/reference.c',wrapper]
hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest()for p in files}
subprocess.run(['/usr/bin/gcc','-O3','-march=native','-fno-builtin-expf','-ffp-contract=off','-DSWIN_HOST','-I'+str(SWIN/'c'),*map(str,files),'-Wl,--wrap=expf','-lm','-o',str(case/'host_opt')],check=True)
items=[]
for image in sorted((BASE/'competition/images').glob('*.JPEG')):
 output=case/(image.name+'.logits');print('NEWLIB_MODEL '+image.name,flush=True)
 argv=[case/'host_opt',plan/'graph.bin',plan/'constants.bin',baseline/(image.name+'.input'),output,tables/'gelu.bin',plan/'gelu-fusions.bin']
 with(case/(image.name+'.log')).open('w')as log:subprocess.run(list(map(str,argv)),stdout=log,stderr=subprocess.STDOUT,timeout=180,check=True)
 expected=(baseline/(image.name+'.baseline')).read_bytes();actual=output.read_bytes();assert len(actual)==len(expected)==4000
 a=struct.unpack('<1000f',actual);b=struct.unpack('<1000f',expected);top_a=max(range(1000),key=a.__getitem__);top_b=max(range(1000),key=b.__getitem__)
 error=max(abs(x-y)for x,y in zip(a,b));peak=max(abs(x)for x in b);bound=2e-6*max(1,peak)
 items.append({'image':image.name,'byte_exact_logits':actual==expected,'top1':top_a,'frozen_C_top1':top_b,'max_abs_error':error,'comparison_bound':bound,'within_bound':error<=bound,'top1_equal':top_a==top_b,'sha256':hashlib.sha256(actual).hexdigest()})
assert len(items)==20
for p,want in hashes.items():assert hashlib.sha256(Path(p).read_bytes()).hexdigest()==want
passed=all(x['within_bound']and x['top1_equal']for x in items)
summary={'status':'PASS'if passed else 'NUMERICAL_DIFFERENCE_REQUIRES_REVIEW','images':20,'byte_exact_images':sum(x['byte_exact_logits']for x in items),'within_bound_images':sum(x['within_bound']for x in items),'top1_equal_images':sum(x['top1_equal']for x in items),'results':items,'source_sha256':hashes,
 'scope':'host full graph with archived installed-path classic Newlib expf replacing glibc expf only, fixed model/quantization/preprocess; numerical tolerance and Top1 comparison to frozen C, not RTL whole graph or official accuracy'}
(case/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print('NEWLIB_FULL_MODEL_REFERENCE_'+('PASS'if passed else'REQUIRES_REVIEW'),flush=True)
