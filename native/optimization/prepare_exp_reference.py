"""Compile the archived primary-source expf as an independent float32 oracle."""
import ctypes,hashlib,json,struct,subprocess
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parent
case=ROOT/'work/exp-reference';case.mkdir(parents=True,exist_ok=True)
source=ROOT/'vendor/newlib-exp-source/newlib/libm/math/ef_exp.c'
text=source.read_text().replace('#include "fdlibm.h"','#include "newlib_exp_shim.h"').replace('#include "math_config.h"','')
text=text.replace('__ieee754_expf','reference_newlib_expf').replace('hy+(k<<23)','hy+((uint32_t)k<<23)').replace('hy+((k+100)<<23)','hy+((uint32_t)(k+100)<<23)')
text+='\nuint32_t reference_exp_bits(uint32_t raw){float x,y;memcpy(&x,&raw,4);y=reference_newlib_expf(x);memcpy(&raw,&y,4);return raw;}\n'
(case/'reference.c').write_text(text)
(case/'newlib_exp_shim.h').write_text('''#include <stdint.h>
#include <string.h>
#define __OBSOLETE_MATH 1
#define GET_FLOAT_WORD(i,x) do{float v=(x);memcpy(&(i),&v,4);}while(0)
#define SET_FLOAT_WORD(x,i) do{uint32_t v=(uint32_t)(i);memcpy(&(x),&v,4);}while(0)
#define FLT_UWORD_IS_NAN(x) ((x)>0x7f800000u)
#define FLT_UWORD_IS_INFINITE(x) ((x)==0x7f800000u)
#define FLT_UWORD_LOG_MAX 0x42b17217
#define FLT_UWORD_LOG_MIN 0x42cff1b5u
static float __math_oflowf(uint32_t sign){volatile float x=0x1p97f;(void)sign;return x*x;}
static float __math_uflowf(uint32_t sign){volatile float x=0x1p-95f;(void)sign;return x*x;}
''')
subprocess.run(['/usr/bin/gcc','-O2','-std=c11','-fno-builtin','-ffp-contract=off','-fexcess-precision=standard','-shared','-fPIC',str(case/'reference.c'),'-o',str(case/'reference.so')],check=True)
lib=ctypes.CDLL(str(case/'reference.so'));oracle=lib.reference_exp_bits;oracle.argtypes=[ctypes.c_uint32];oracle.restype=ctypes.c_uint32
rng=np.random.default_rng(0x45585046)
special=[0,0x80000000,1,0x80000001,0x007fffff,0x00800000,0x7f7fffff,0xff7fffff,0x3f800000,0xbf800000,0x7f800000,0xff800000,0x7fc00000,0x7f800001]
values=list(special)+[int(x)for x in rng.integers(0,2**32,size=8192,dtype=np.uint32)]
values += [int(x)for x in rng.uniform(-105,89,size=10000).astype(np.float32).view(np.uint32)]
for boundary in [0x3eb17218,0x3f851592,0x34000000,0x42b17217,0x42cff1b5]:
 for delta in range(-32,33):values += [boundary+delta,(boundary+delta)|0x80000000]
for k in range(-150,129):
 raw=int(np.array([np.float32(k*np.log(2.0))],np.float32).view(np.uint32)[0])
 for delta in range(-2,3):values.append((raw+delta)&0xffffffff)
records=[]
for raw in values:
 answer=int(oracle(raw));nan=int(answer&0x7fffffff>0x7f800000)
 records.append((raw,answer,nan))
(case/'vectors.hex').write_text(''.join('%08x %08x %x\n'%r for r in records))
summary={'status':'ORACLE_AND_VECTORS_GENERATED_RTL_PENDING','vectors':len(records),'source':str(source),
 'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'adaptations':['portable memcpy float bit access','numeric overflow/underflow helpers without errno','unsigned exponent shifts with the installed RV32 two-complement bit result'],
 'scope':'archived classic Newlib algorithm compiled as float32 oracle; numerical match to installed RV32 expf and RTL tested separately'}
(case/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print('EXP_REFERENCE_READY',len(records),flush=True)
