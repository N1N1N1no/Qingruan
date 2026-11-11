"""Reproduce the documented CUDA baseline on the available local GPU.

GPU differs from the provider's A100; preserve that distinction in the evidence.
"""
import csv,hashlib,importlib.util,json,os,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent;BASE=ROOT.parents[1]
sys.path.insert(0,str(ROOT/'gpu-runtime'))
import numpy as np
import onnxruntime as ort
ort.preload_dlls(directory='')
case=ROOT/'work/cuda-reference';case.mkdir(parents=True,exist_ok=True)
competition=BASE/'competition';model=competition/'models/swin_tiny_patch4_window7_224_imagenet1k_int8.onnx'
spec=importlib.util.spec_from_file_location('unchanged_preprocess',competition/'official_preprocess.py');pre=importlib.util.module_from_spec(spec);spec.loader.exec_module(pre)
options=ort.SessionOptions();options.graph_optimization_level=ort.GraphOptimizationLevel.ORT_DISABLE_ALL
options.execution_mode=ort.ExecutionMode.ORT_SEQUENTIAL;options.intra_op_num_threads=1;options.inter_op_num_threads=1
options.enable_profiling=True;options.profile_file_prefix=str(case/'ort-profile')
provider={'use_tf32':'0'}
session=ort.InferenceSession(str(model),options,providers=[('CUDAExecutionProvider',provider),'CPUExecutionProvider'])
if 'CUDAExecutionProvider' not in session.get_providers():raise RuntimeError('CUDA provider failed; refuse CPU-only fallback')
results=[]
with (competition/'reference_top1_5.csv').open(encoding='utf-8-sig') as f:
 for row in csv.DictReader(f):
  x=pre.prepare_classification_input('swin_tiny_patch4_window7_224',competition/'images'/row['image'],False)
  started=time.monotonic();y=session.run(None,{'input':x})[0];np.save(case/(row['image']+'.logits.npy'),y)
  pred=int(y.argmax());expected=int(row['Swin-Tiny']);results.append({'image':row['image'],'actual':pred,'official':expected,'match':pred==expected,'elapsed_s':time.monotonic()-started})
  print('CUDA_SAMPLE',results[-1],flush=True)
profile=Path(session.end_profiling());events=json.loads(profile.read_text());counts={}
for e in events:
 p=e.get('args',{}).get('provider')
 if p:counts[p]=counts.get(p,0)+1
assert counts.get('CUDAExecutionProvider',0)>0,'No kernels actually ran on CUDA'
gpu=subprocess.run(['nvidia-smi','--query-gpu=name,compute_cap','--format=csv,noheader'],capture_output=True,text=True,check=True).stdout.strip()
summary={'status':'PASS' if all(r['match'] for r in results) else 'REFERENCE_MISMATCH','scope':'original ONNX/preprocess, CUDA EP 1.23.2, graph optimization disabled, TF32 off; local GPU is not provider A100',
         'gpu':gpu,'ort_version':ort.__version__,'providers':session.get_providers(),'provider_options':session.get_provider_options(),
         'kernel_event_counts_by_provider':counts,'results':results,'matches':sum(r['match'] for r in results),'samples':len(results),
         'model_sha256':hashlib.sha256(model.read_bytes()).hexdigest(),'preprocess_sha256':hashlib.sha256((competition/'official_preprocess.py').read_bytes()).hexdigest()}
(case/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print('CUDA_BASELINE_STATUS='+summary['status'],flush=True)
