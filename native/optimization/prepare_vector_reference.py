"""FP32 vector reference for arithmetic, DQ and MAC postscale, without FMA."""
import ctypes,hashlib,json,subprocess
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parent;case=ROOT/'work/vector-reference';case.mkdir(parents=True,exist_ok=True)
source=case/'reference.c';source.write_text('''#include <stdint.h>
#include <string.h>
uint32_t reference_vector(uint32_t op,uint32_t a,uint32_t b,uint32_t c,uint32_t d,uint32_t zero){
 float x,y,z,bias,v;memcpy(&x,&a,4);memcpy(&y,&b,4);memcpy(&z,&c,4);memcpy(&bias,&d,4);
 switch(op){
 case 0:v=x+y;break;case 1:v=x*y;break;case 2:v=x/y;break;
 case 3:v=(float)((int32_t)(int8_t)a-(int32_t)(int8_t)zero)*y;break;
 case 4:v=(float)((int32_t)(uint8_t)a-(int32_t)(uint8_t)zero)*y;break;
 case 5:v=(float)(int32_t)a*y;break;
 case 6:v=(float)(int32_t)a*y*z;break;
 case 7:v=(float)(int32_t)a*y*z+bias;break;
 default:return 0;
 }memcpy(&a,&v,4);return a;
}
''')
subprocess.run(['/usr/bin/gcc','-O2','-std=c11','-fno-builtin','-ffp-contract=off','-fexcess-precision=standard','-shared','-fPIC',source,'-o',case/'reference.so'],check=True)
lib=ctypes.CDLL(str(case/'reference.so'));fn=lib.reference_vector;fn.argtypes=[ctypes.c_uint32]*6;fn.restype=ctypes.c_uint32
rng=np.random.default_rng(0x56454354);special=[0,0x80000000,1,0x80000001,0x007fffff,0x00800000,0x7f7fffff,0xff7fffff,0x3f800000,0xbf800000,0x7f800000,0xff800000,0x7fc00000,0x7f800001]
inputs=[]
for op in range(8):
 for a in special:
  for b in special:inputs.append((op,a,b,0x3f000000,0x3f800000,127 if op==3 else 255 if op==4 else 0))
 for i in range(1024):
  a,b,c,d=map(int,rng.integers(0,2**32,4,dtype=np.uint32));zero=int(rng.integers(0,256))if op in[3,4]else 0
  inputs.append((op,a,b,c,d,zero))
for op in [5,6,7]:
 for a in [-2147483648,-2147483647,-16777217,-16777216,-16777215,0,16777215,16777216,16777217,2147483646,2147483647]:
  for scale in special:inputs.append((op,a&0xffffffff,scale,0x3f800001,0xbf800000,0))
inputs += [(5,0x12345678,0x3f800000,0,0,1)]+[(op,0,0,0,0,0)for op in range(8,16)]
payload=[]
for row in inputs:
 op,a,b,c,d,zero=row;error=int(op>7 or op==5 and zero!=0);answer=0 if error else int(fn(*row))
 payload.append('%x %08x %08x %08x %08x %08x %08x %x\n'%(*row,answer,error))
(case/'vectors.txt').write_text(''.join(payload))
summary={'status':'ORACLE_GENERATED_RTL_PENDING','vectors':len(inputs),'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
 'scope':'ordered C float32 Add/Mul/Div, signed/unsigned8 DQ, zero-offset signed32 DQ, signed32 MAC postscale with optional bias; invalid operations/unsupported signed32 zero offsets rejected'}
(case/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print('VECTOR_REFERENCE_READY',len(inputs),flush=True)
