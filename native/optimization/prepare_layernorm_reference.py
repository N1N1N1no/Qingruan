"""Ordered float32 LayerNorm reference for the shared arithmetic controller."""
import ctypes,hashlib,json,subprocess
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parent;case=ROOT/'work/layernorm-reference';case.mkdir(parents=True,exist_ok=True)
source=case/'reference.c';source.write_text('''#include <stdint.h>
#include <string.h>
#include <math.h>
void reference_layernorm(const float *x,const float *scale,const float *bias,float *out,uint32_t n,float eps,float *stats){
 float sum=0,ss=0;
 for(uint32_t i=0;i<n;++i){sum+=x[i];ss+=x[i]*x[i];}
 float mean=sum/(float)n,var=ss/(float)n-mean*mean;
 float inverse=1.0f/sqrtf(fmaxf(var,0.0f)+eps);
 stats[0]=mean;stats[1]=inverse;
 for(uint32_t i=0;i<n;++i)out[i]=(x[i]-mean)*inverse*scale[i]+bias[i];
}
''')
subprocess.run(['/usr/bin/gcc','-O2','-std=c11','-fno-builtin','-ffp-contract=off','-fexcess-precision=standard','-shared','-fPIC',source,'-lm','-o',case/'reference.so'],check=True)
lib=ctypes.CDLL(str(case/'reference.so'));fn=lib.reference_layernorm;ptr=ctypes.POINTER(ctypes.c_float)
fn.argtypes=[ptr,ptr,ptr,ptr,ctypes.c_uint32,ctypes.c_float,ptr]
rng=np.random.default_rng(0x4c415945);rows=[]
for n in [1,2,3,7,49,96,127,128,129,192,255,256,257,384,511,512,513,768,1024,1536]:
 for mode in range(4):
  x=rng.uniform(-10,10,n).astype(np.float32)
  if mode==1:x.fill(np.float32(3.1415927))
  if mode==2:x=(np.float32(1024)+rng.uniform(-0.0001,0.0001,n).astype(np.float32)).astype(np.float32)
  if mode==3:x=rng.uniform(-1e-38,1e-38,n).astype(np.float32)
  scale=rng.uniform(-2,2,n).astype(np.float32);bias=rng.uniform(-1,1,n).astype(np.float32)
  rows.append((x,scale,bias,np.float32(1e-5)))
special=[0,0x80000000,1,0x80000001,0x007fffff,0x00800000,0x7f7fffff,0xff7fffff,0x7f800000,0xff800000,0x7fc00000,0x7f800001]
for raw in special:
 x=np.array([raw]*3,np.uint32).view(np.float32);rows.append((x,np.ones(3,np.float32),np.zeros(3,np.float32),np.float32(1e-5)))
for eps in [0,-1,1e-30,np.inf,np.nan]:
 x=np.zeros(3,np.float32);rows.append((x,np.ones(3,np.float32),np.zeros(3,np.float32),np.float32(eps)))
payload=[];items=[]
for x,scale,bias,eps in rows:
 out=np.empty_like(x);stats=np.empty(2,np.float32)
 fn(x.ctypes.data_as(ptr),scale.ctypes.data_as(ptr),bias.ctypes.data_as(ptr),out.ctypes.data_as(ptr),len(x),ctypes.c_float(eps),stats.ctypes.data_as(ptr))
 payload.append('%d %08x %08x %08x\n'%(len(x),int(np.array([eps],np.float32).view(np.uint32)[0]),*map(int,stats.view(np.uint32))))
 payload.append(''.join('%08x %08x %08x %08x\n'%r for r in zip(x.view(np.uint32),scale.view(np.uint32),bias.view(np.uint32),out.view(np.uint32))))
 items.append({'elements':len(x),'epsilon_raw':int(np.array([eps],np.float32).view(np.uint32)[0])})
(case/'vectors.txt').write_text(''.join(payload))
summary={'status':'ORACLE_GENERATED_RTL_PENDING','rows':len(rows),'elements':sum(len(x[0])for x in rows),'widths':sorted(set(len(x[0])for x in rows)),
 'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'scope':'same ordered float32 sum/ss, clamp, sqrt, inverse and separate output arithmetic as graph.c; NaN class comparison, no FMA, no full inference'}
(case/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print('LAYERNORM_REFERENCE_READY',len(rows),flush=True)
