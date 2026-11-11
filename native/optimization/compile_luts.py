"""Prove two model-specific, image-independent non-MAC lookup candidates.

GELU is a finite 8-bit domain per channel. Quantization thresholds preserve the
existing float32 division + ties-to-even behavior, rather than approximating by
reciprocal multiplication.
"""
import ctypes,json,math,struct,subprocess
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parent;BASE=ROOT.parents[1]
source=BASE/'native/swin-tiny/work/verify.ylnpqxth';plan=json.loads((source/'plan.json').read_text());nodes=plan['schedule'];tensors=plan['tensors']
constants=(source/'constants.bin').read_bytes();case=ROOT/'work/lookup-tables';case.mkdir(parents=True,exist_ok=True)
def const(i):
 t=tensors[i];assert t['storage']=='constant'
 return np.frombuffer(constants[t['offset']:t['offset']+t['bytes']],dtype=t['dtype']).copy().reshape(t['shape'])
subprocess.run(['/usr/bin/gcc','-O2','-ffp-contract=off','-fPIC','-shared',str(ROOT/'c/gelu_tables.c'),'-lm','-o',str(case/'gelu_tables.so')],check=True)
lib=ctypes.CDLL(str(case/'gelu_tables.so'));fn=lib.make_gelu_tables
ptr=ctypes.POINTER(ctypes.c_float);fn.argtypes=[ctypes.c_void_p,ctypes.c_uint32,ptr,ctypes.c_float,ctypes.c_int32,ctypes.c_float,ctypes.c_float,ctypes.c_float,ctypes.c_float,ctypes.c_int32]
expected_ops=['DequantizeLinear','Add','Div','Erf','Add','Mul','Mul','QuantizeLinear'];fusions=[];blob=bytearray()
consumers={}
for n in nodes:
 for i in n['inputs']:consumers.setdefault(i,[]).append(n['node_index'])
for erf in [n for n in nodes if n['op']=='Erf']:
 start=erf['node_index']-3;span=nodes[start:start+8];assert [n['op'] for n in span]==expected_ops
 dq,add,div,erf,plus,mul,half,q=span
 raw,ins,inz=dq['inputs'];bias=next(i for i in add['inputs'] if i!=dq['outputs'][0])
 assert dq['outputs'][0] in add['inputs'] and div['inputs'][0]==add['outputs'][0] and erf['inputs']==div['outputs']
 assert plus['inputs'][0]==erf['outputs'][0] and mul['inputs']==[add['outputs'][0],plus['outputs'][0]]
 assert half['inputs'][0]==mul['outputs'][0] and q['inputs'][0]==half['outputs'][0]
 ids={n['node_index'] for n in span}
 assert all(all(j in ids for j in consumers[n['outputs'][0]]) for n in span[:-1])
 channels=tensors[raw]['shape'][-1];bias_array=const(bias);assert bias_array.shape== (channels,)
 assert tensors[raw]['dtype']=='int8' and tensors[q['outputs'][0]]['dtype']=='int8'
 scales=[const(i) for i in [ins,inz,div['inputs'][1],plus['inputs'][1],half['inputs'][1],q['inputs'][1],q['inputs'][2]]]
 assert all(a.shape==() for a in scales)
 si,zi,den,one,hf,so,zo=[a.item() for a in scales]
 table=np.empty((channels,256),np.uint8)
 fn(table.ctypes.data,channels,bias_array.ctypes.data_as(ptr),si,zi,den,one,hf,so,zo)
 record={'start':start,'end':q['node_index'],'raw_input':raw,'output':q['outputs'][0],'channels':channels,
         'elements':math.prod(tensors[raw]['shape']),'offset':len(blob),'table_bytes':table.nbytes,
         'input_scale':si,'input_zero':zi,'output_scale':so,'output_zero':zo,
         'scope':'all 256 raw INT8 inputs for every original bias channel; no image or label used'}
 blob+=table.tobytes();fusions.append(record)
assert len(fusions)==12
(case/'gelu.bin').write_bytes(blob);(case/'gelu-fusions.json').write_text(json.dumps(fusions,indent=2)+'\n')
(case/'gelu-fusions.bin').write_bytes(b''.join(struct.pack('<7I',*[r[k] for k in ['start','end','raw_input','output','channels','elements','offset']]) for r in fusions))

# QuantizeLinear's scalar-scale domains allow exact monotonic transition tables.
quant=[];threshold_blob=bytearray();np.seterr(all='ignore');rng=np.random.default_rng(0x514e54)
for n in [n for n in nodes if n['op']=='QuantizeLinear']:
 scale=const(n['inputs'][1]);zero=const(n['inputs'][2]);assert scale.shape==zero.shape==() and scale>0
 typ=tensors[n['outputs'][0]]['dtype'];assert typ in ['int8','uint8'];minimum=-128 if typ=='int8' else 0;maximum=minimum+255
 targets=np.arange(minimum+1,maximum+1,dtype=np.float32)
 lo=np.full(255,0x00800000,np.uint64);hi=np.full(255,0xff7fffff,np.uint64)
 def floats(keys):
  u=keys.astype(np.uint32);bits=np.where(u&0x80000000,u^np.uint32(0x80000000),~u).astype(np.uint32);return bits.view(np.float32)
 def reference(x):return np.clip(np.rint(np.divide(x,scale,dtype=np.float32))+zero,minimum,maximum)
 for _ in range(32):
  mid=(lo+hi)//2;greater=reference(floats(mid))>=targets
  hi=np.where(greater,mid,hi);lo=np.where(greater,lo,mid+1)
 assert np.array_equal(lo,hi)
 thresholds=lo.astype(np.uint32);assert np.all(thresholds[1:]>thresholds[:-1])
 assert np.all(reference(floats(thresholds))>=targets) and np.all(reference(floats(thresholds.astype(np.uint64)-1))<targets)
 # All transition neighbors plus independent random raw floats and +/-infinity.
 near=(thresholds.astype(np.int64)[:,None]+np.arange(-4,5)[None,:]).ravel().astype(np.uint32)
 random=rng.integers(0x007fffff,0xff800001,size=32768,dtype=np.uint32)
 keys=np.concatenate([near,random,np.array([0x007fffff,0x7fffffff,0x80000000,0xff800000],np.uint32)])
 x=floats(keys);actual=np.searchsorted(thresholds,keys,side='right')+minimum
 assert np.array_equal(actual,reference(x).astype(np.int32)),n['name']
 # Hardware stores thresholds in breadth-first binary-search tree order.
 heap=np.zeros(255,np.uint32)
 def tree(left,right,node):
  if left>right:return
  mid=(left+right)//2;heap[node-1]=thresholds[mid];tree(left,mid-1,node*2);tree(mid+1,right,node*2+1)
 tree(0,254,1)
 quant.append({'node':n['node_index'],'name':n['name'],'scale':float(scale),'zero':int(zero),'dtype':typ,
               'threshold_offset':len(threshold_blob),'transition_boundaries_checked':255,'samples_checked':len(keys)})
 threshold_blob+=heap.astype('<u4').tobytes()
(case/'quant-thresholds.bin').write_bytes(threshold_blob);(case/'quant-thresholds.json').write_text(json.dumps(quant,indent=2)+'\n')
summary={'status':'COMPILED_QUANT_PROOF_PASS_GELU_EXECUTION_PENDING','GELU_fusions':len(fusions),'GELU_nodes_replaced':len(fusions)*8,
         'GELU_table_bytes':len(blob),'GELU_domain_values':sum(r['channels']*256 for r in fusions),
         'GELU_runtime_elements':sum(r['elements'] for r in fusions),'quantizers':len(quant),'quant_threshold_bytes':len(threshold_blob),
         'quant_transition_boundaries_verified':255*len(quant),'quant_independent_and_boundary_samples':sum(r['samples_checked'] for r in quant),
         'quant_proof':'float32 division/rint/clamp is monotone; every exact transition and its predecessor checked, then boundary/random validation',
         'scope':'offline model transform candidates; no end-to-end hardware or accuracy claim'}
(case/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary),flush=True)
