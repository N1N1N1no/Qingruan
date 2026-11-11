"""Check every finite GELU domain point through the unchanged C dispatcher,
then check fused full-model execution against the baseline on all 20 images.
"""
import importlib.util,json,math,struct,subprocess
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parent;BASE=ROOT.parents[1];SWIN=BASE/'native/swin-tiny'
source=SWIN/'work/verify.ylnpqxth';plan=json.loads((source/'plan.json').read_text());tensors=plan['tensors'];nodes=plan['schedule']
tablecase=ROOT/'work/lookup-tables';fusions=json.loads((tablecase/'gelu-fusions.json').read_text());lookup=(tablecase/'gelu.bin').read_bytes()
constants=(source/'constants.bin').read_bytes();case=ROOT/'work/fusion-proof';case.mkdir(parents=True,exist_ok=True)
def run(args,label):
 print('FUSION_PROOF='+label,flush=True)
 with (case/(label+'.log')).open('w') as f:r=subprocess.run(list(map(str,args)),cwd=case,stdout=f,stderr=subprocess.STDOUT,timeout=1200)
 if r.returncode:raise RuntimeError(label+' failed')
run(['/usr/bin/gcc','-O3','-march=native','-ffp-contract=off','-DSWIN_HOST','-I'+str(SWIN/'c'),SWIN/'c/graph.c',SWIN/'c/pe_driver.c',ROOT/'c/host_opt.c','-lm','-o',case/'host_opt'],'compile')
opcode=['Constant','Identity','Reshape','Flatten','Shape','Transpose','Slice','Gather','Concat','Add','Mul','Div','Erf','LayerNormalization','Softmax','GlobalAveragePool','QuantizeLinear','DequantizeLinear','Conv','MatMul','Gemm']
dtype={'float32':1,'int8':3,'uint8':2,'int32':6,'int64':7};domain_checks=[]
for f in fusions:
 span=nodes[f['start']:f['end']+1];ids=list(dict.fromkeys([i for n in span for i in n['inputs']+n['outputs']]));local={i:j for j,i in enumerate(ids)}
 constdata=bytearray();tensorbytes=bytearray();arena=0;channels=f['channels']
 for i in ids:
  t=tensors[i];sh=[256,channels] if i==f['raw_input'] or any(i in n['outputs'] for n in span) else t['shape'];elements=math.prod(sh);nbytes=elements*np.dtype(t['dtype']).itemsize
  if t['storage']=='constant':
   offset=(len(constdata)+63)//64*64;constdata+=b'\0'*(offset-len(constdata));constdata+=constants[t['offset']:t['offset']+t['bytes']];storage=0
  else:offset=arena;arena+=(nbytes+63)//64*64;storage=1
  tensorbytes+=struct.pack('<6I8i',offset,elements,dtype[t['dtype']],len(sh),storage,0,*(sh+[1]*(8-len(sh))))
 nodebytes=bytearray()
 for n in span:
  ins=[local[i] for i in n['inputs']];attr=[0]*16;attr[0]=n['attributes'].get('axis',1)
  nodebytes+=struct.pack('<4I8i16i4fi',opcode.index(n['op']),1,len(ins),local[n['outputs'][0]],*(ins+[-1]*(8-len(ins))),*attr,0,0,0,0,-1)
 head=struct.pack('<16I',0x4e495753,1,len(ids),len(span),0,local[f['raw_input']],local[f['output']],64,64+len(tensorbytes),64+len(tensorbytes)+len(nodebytes),len(constdata),arena,0,0,0,0)
 prefix=case/('domain_%04d'%f['start']);graph=prefix.with_suffix('.graph');weights=prefix.with_suffix('.const');inp=prefix.with_suffix('.input');out=prefix.with_suffix('.output')
 graph.write_bytes(head+tensorbytes+nodebytes);weights.write_bytes(constdata);np.broadcast_to(np.arange(256,dtype=np.uint8)[:,None],(256,channels)).copy().tofile(inp)
 run([case/'host_opt',graph,weights,inp,out],'domain_%04d'%f['start'])
 actual=np.fromfile(out,np.uint8).reshape(256,channels).T.copy();expected=np.frombuffer(lookup[f['offset']:f['offset']+f['table_bytes']],np.uint8).reshape(channels,256)
 assert np.array_equal(actual,expected),f
 domain_checks.append({'start':f['start'],'channels':channels,'checked_values':actual.size,'bit_exact':True})
spec=importlib.util.spec_from_file_location('original_preprocess',BASE/'competition/official_preprocess.py');pre=importlib.util.module_from_spec(spec);spec.loader.exec_module(pre)
samples=[]
for image in sorted((BASE/'competition/images').glob('*.JPEG')):
 x=pre.prepare_classification_input('swin_tiny_patch4_window7_224',image,False);inputfile=case/(image.name+'.input');x.tofile(inputfile)
 a=case/(image.name+'.baseline');b=case/(image.name+'.fused')
 run([source/'host_run',source/'graph.bin',source/'constants.bin',inputfile,a],image.name+'_baseline')
 run([case/'host_opt',source/'graph.bin',source/'constants.bin',inputfile,b,tablecase/'gelu.bin',tablecase/'gelu-fusions.bin'],image.name+'_fused')
 baseline=np.fromfile(a,np.float32);fused=np.fromfile(b,np.float32);assert a.read_bytes()==b.read_bytes(),image.name
 samples.append({'image':image.name,'bit_exact_logits':True,'top1':int(fused.argmax())})
summary={'status':'PASS','GELU_domain_values_checked':sum(x['checked_values'] for x in domain_checks),'domain_checks':domain_checks,
         'full_model_samples':len(samples),'samples':samples,'baseline':'unchanged complete C graph with exact integer MACs',
         'scope':'GELU fusion adds no error relative to C baseline; not a claim of official-reference or full SoC accuracy',
         'hardware_executed':False}
assert len(samples)==20
(case/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print('GELU_FUSION_ALL20_BIT_EXACT_PASS',flush=True)
