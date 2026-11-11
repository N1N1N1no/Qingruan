"""Vector DMA cases: row broadcasts, scalar parameters and all67 model DQs."""
import ctypes,hashlib,json,struct
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parent;case=ROOT/'work/vector-dma-reference';case.mkdir(parents=True,exist_ok=True)
lib=ctypes.CDLL(str(ROOT/'work/vector-reference/reference.so'));fn=lib.reference_vector;fn.argtypes=[ctypes.c_uint32]*6;fn.restype=ctypes.c_uint32
rng=np.random.default_rng(0x56444d41);cases=[]
def add(op,flags,n,rows,mask,zero=0,aa=None,bb=None,cc=None,scalar_b=0x3d000000,scalar_c=0x3f800001,scalar_d=0x3e000000,label='random'):
 count=n*rows
 if aa is None:
  aa=rng.integers(0,256 if op in[3,4]else 2**32,count,dtype=np.uint32)
  if op<3:aa=rng.uniform(-10,10,count).astype(np.float32).view(np.uint32)
 if bb is None:bb=rng.uniform(0.25,2,count).astype(np.float32).view(np.uint32)
 if cc is None:cc=rng.uniform(-1,1,count).astype(np.float32).view(np.uint32)
 aa,bb,cc=[np.asarray(x,dtype=np.uint32).reshape(-1)for x in[aa,bb,cc]];assert all(len(x)==count for x in[aa,bb,cc])
 result=[]
 for r in range(rows):
  for i in range(n):
   a=int(aa[(0 if mask&1 else r)*n+i]);b=int(bb[(0 if mask&2 else r)*n+i]);c=int(cc[(0 if mask&4 else r)*n+i])
   if op>=6:args=(op,a,scalar_b,scalar_c if flags&1 else b,scalar_d if flags&2 else c,zero)
   else:args=(op,a,scalar_b if flags&1 else b,0,0,zero)
   result.append(int(fn(*args)))
 cases.append({'op':op,'flags':flags,'n':n,'rows':rows,'broadcast':mask,'zero':zero,'scalar_b':scalar_b,'scalar_c':scalar_c,'scalar_d':scalar_d,
  'label':label,'aa':aa,'bb':bb,'cc':cc,'result':result})
for op in range(8):
 for i,n in enumerate([1,7,128,129,257,1536]):
  flags=((i+op)&1)|(2 if op==7 and i%3==0 else 0)
  add(op,flags,n,2,(i+op)%8,zero=(0xffffff80 if op==3 else 255 if op==4 else 0))
for flags in range(4):add(7,flags,129,3,flags,label='postscale_parameter_modes')
raw=(ROOT/'work/fused-plan/graph.bin').read_bytes();h=struct.unpack_from('<16I',raw);constants=(ROOT/'work/fused-plan/constants.bin').read_bytes()
ts=[struct.unpack_from('<6I8i',raw,h[7]+i*56)for i in range(h[2])]
metadata=json.loads((ROOT/'nonmac-architecture-inputs.json').read_text());model_dq=0
for item in metadata['nodes']:
 if item['op']!=17:continue
 node=struct.unpack_from('<4I8i',raw,h[8]+item['node']*132);a,scale,z=[ts[tid]for tid in node[4:7]]
 assert scale[4]==z[4]==0
 if a[2]in[2,3]:
  assert scale[1]==z[1]==1
  scalar=struct.unpack_from('<I',constants,scale[0])[0];zero=struct.unpack_from('<b'if z[2]==3 else'<B',constants,z[0])[0]&0xffffffff
  add(3 if a[2]==3 else 4,1,256,1,0,zero=zero,aa=np.arange(256,dtype=np.uint32),scalar_b=scalar,label='model_DQ_node_'+str(item['node']))
 else:
  assert a[2]==6 and a[4]==0 and not any(constants[z[0]:z[0]+z[1]*4])and scale[1]==a[1]
  aa=np.frombuffer(constants[a[0]:a[0]+a[1]*4],dtype='<u4').copy();bb=np.frombuffer(constants[scale[0]:scale[0]+scale[1]*4],dtype='<u4').copy()
  add(5,0,a[1],1,0,aa=aa,bb=bb,label='model_DQ_node_'+str(item['node']))
 model_dq+=1
assert model_dq==67
add(2,0,17,3,0,label='fault_base')
lines=[];manifest=[]
for i,c in enumerate(cases):
 lines.append('%d %d %d %d %d %d %08x %08x %08x %08x\n'%(i,c['op'],c['flags'],c['n'],c['rows'],c['broadcast'],c['zero'],c['scalar_b'],c['scalar_c'],c['scalar_d']))
 lines.append(''.join('%08x %08x %08x %08x\n'%row for row in zip(c['aa'],c['bb'],c['cc'],c['result'])))
 manifest.append({k:v for k,v in c.items()if k not in['aa','bb','cc','result']})
(case/'vectors.txt').write_text(''.join(lines))
summary={'status':'REFERENCE_GENERATED_DMA_RTL_PENDING','cases':len(cases),'values':sum(c['n']*c['rows']for c in cases),'model_DQ_nodes':model_dq,'descriptions':manifest,
 'source_sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest()for p in[ROOT/'work/vector-reference/reference.c',ROOT/'work/fused-plan/graph.bin',ROOT/'work/fused-plan/constants.bin']},
 'scope':'actual67 DQ parameter groups (all raw8 codes and original S32 bias data), scalar/vector row-broadcast arithmetic and postscale test vectors, no inference'}
(case/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print('VECTOR_DMA_REFERENCE_READY cases='+str(len(cases))+' model_DQ=67',flush=True)
