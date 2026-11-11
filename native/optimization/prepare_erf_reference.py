"""Primary-source classic Newlib erff, for module-level GELU equivalence."""
import ctypes,hashlib,json,re,subprocess
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parent;case=ROOT/'work/erf-reference';case.mkdir(parents=True,exist_ok=True)
original=ROOT/'vendor/newlib-exp-source/newlib/libm/math/sf_erf.c';text=original.read_text()
cut=text.index('#ifdef __STDC__\n\tfloat erfcf');text=text[:cut]
text=text.replace('#include "fdlibm.h"','#include "newlib_erf_shim.h"').replace('#include "math_config.h"','')
text=re.sub(r'\berff\b','reference_newlib_erff',text).replace('__ieee754_expf','reference_newlib_expf')
text+='\nuint32_t reference_erf_bits(uint32_t raw){float x,y;memcpy(&x,&raw,4);y=reference_newlib_erff(x);memcpy(&raw,&y,4);return raw;}\n'
(case/'reference.c').write_text(text)
(case/'newlib_erf_shim.h').write_text('#include "../exp-reference/newlib_exp_shim.h"\n#include <math.h>\n#define FLT_UWORD_IS_FINITE(x) ((uint32_t)(x)<0x7f800000u)\nextern float reference_newlib_expf(float);\n')
subprocess.run(['/usr/bin/gcc','-O2','-fno-builtin','-ffp-contract=off','-fexcess-precision=standard','-shared','-fPIC',case/'reference.c',ROOT/'work/exp-reference/reference.c','-lm','-o',case/'reference.so'],check=True)
lib=ctypes.CDLL(str(case/'reference.so'));fn=lib.reference_erf_bits;fn.argtypes=[ctypes.c_uint32];fn.restype=ctypes.c_uint32
rng=np.random.default_rng(0x45524646)
values=[0,0x80000000,1,0x80000001,0x007fffff,0x00800000,0x7f7fffff,0xff7fffff,0x3f800000,0xbf800000,0x7f800000,0xff800000,0x7fc00000,0x7f800001]
values += list(map(int,rng.integers(0,2**32,4096,dtype=np.uint32)))
values += list(map(int,rng.uniform(-6.1,6.1,6000).astype(np.float32).view(np.uint32)))
for boundary in [0x3f580000,0x3fa00000,0x40c00000,0x31800000,0x4036db6e,0x04000000]:
 for delta in range(-32,33):values += [boundary+delta,(boundary+delta)|0x80000000]
records=[]
for raw in values:
 want=int(fn(raw));records.append((raw,want,int((want&0x7fffffff)>0x7f800000)))
(case/'vectors.hex').write_text(''.join('%08x %08x %x\n'%row for row in records))
summary={'status':'SOURCE_ORACLE_GENERATED_INSTALLED_RV32_IDENTITY_PENDING','vectors':len(records),'source_sha256':hashlib.sha256(original.read_bytes()).hexdigest(),
 'adaptations':['portable bit access via existing audited exp shim','only erff retained; unrelated erfc/double wrappers excluded','classic expf linked from separately verified source; no FMA'],
 'scope':'module math reference only, not full inference or official accuracy; installed RV32 erff numerical comparison required'}
(case/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print('ERF_REFERENCE_READY',len(records),flush=True)
