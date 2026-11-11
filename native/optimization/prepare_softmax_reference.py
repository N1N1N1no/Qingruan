"""Sequential float32 Softmax oracle using the archived classic Newlib expf."""
import ctypes,hashlib,json,struct,subprocess
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parent
case=ROOT/'work/softmax-reference';case.mkdir(parents=True,exist_ok=True)
source=case/'reference.c'
source.write_text('''#include <stdint.h>
#include <string.h>
#include <math.h>
extern float reference_newlib_expf(float);
void reference_softmax(const uint32_t *raw,uint32_t *out,uint32_t n){
 float x[1024],e[1024],maximum=-INFINITY,sum=0;
 for(uint32_t i=0;i<n;++i){memcpy(x+i,raw+i,4);maximum=fmaxf(maximum,x[i]);}
 for(uint32_t i=0;i<n;++i){e[i]=reference_newlib_expf(x[i]-maximum);sum+=e[i];}
 for(uint32_t i=0;i<n;++i){float v=e[i]/sum;memcpy(out+i,&v,4);}
}
''')
subprocess.run(['/usr/bin/gcc','-O2','-std=c11','-fno-builtin','-ffp-contract=off','-fexcess-precision=standard','-shared','-fPIC',
 str(source),str(ROOT/'work/exp-reference/reference.c'),'-lm','-o',str(case/'reference.so')],check=True)
lib=ctypes.CDLL(str(case/'reference.so'));fn=lib.reference_softmax
fn.argtypes=[ctypes.POINTER(ctypes.c_uint32),ctypes.POINTER(ctypes.c_uint32),ctypes.c_uint32]
rng=np.random.default_rng(0x534f4654)
rows=[]
for n in [1,2,3,7,8,9,49,127,128,129,255,256,257,511,512,513,1023,1024]:
 rows.append(rng.uniform(-100,100,n).astype(np.float32).view(np.uint32).tolist())
 rows += [[0]*n,[0x3f800000]*n]
special=[0,0x80000000,1,0x80000001,0x007fffff,0x00800000,0x7f7fffff,0xff7fffff,0x7f800000,0xff800000,0x7fc00000,0x7f800001]
rows += [[x]for x in special]+[special,[0xff800000]*49,[0x7fc00000]*49,[0x7f800000]*49]
for i in range(80):
 if i%4==0:raw=rng.integers(0,2**32,49,dtype=np.uint32)
 else:raw=rng.uniform(-[0,1,10,100][i%4],[0,1,10,100][i%4],49).astype(np.float32).view(np.uint32)
 rows.append(raw.tolist())
captured=ROOT/'work/newlib-difference/threshold-row-input.bin'
if captured.is_file():
 raw=list(struct.unpack('<49I',captured.read_bytes()))
 a=(ctypes.c_uint32*49)(*raw);b=(ctypes.c_uint32*49)();fn(a,b,49)
 assert bytes(b)==(captured.parent/'threshold-row-newlib.bin').read_bytes()
 rows.append(raw)
records=[];payload=[]
for raw in rows:
 n=len(raw);a=(ctypes.c_uint32*n)(*raw);b=(ctypes.c_uint32*n)();fn(a,b,n)
 records.append({'channels':n,'nan_outputs':sum((x&0x7fffffff)>0x7f800000 for x in b)})
 payload.append('%d\n'%n+''.join('%08x %08x\n'%(x,y)for x,y in zip(raw,b)))
(case/'vectors.txt').write_text(''.join(payload))
summary={'status':'ORACLE_GENERATED_RTL_PENDING','rows':len(rows),'values':sum(map(len,rows)),
 'row_widths':sorted(set(map(len,rows))),'source_sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest()for p in[source,ROOT/'work/exp-reference/reference.c']},
 'scope':'same ordered max/exp/sequential sum/div algorithm as the frozen C graph; classic Newlib expf, all float32, no FMA'}
(case/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print('SOFTMAX_REFERENCE_READY',len(rows),flush=True)
