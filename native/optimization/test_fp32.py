"""Independent NumPy IEEE results plus explicit exception/backpressure cases."""
import json,struct,subprocess
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parent;BASE=ROOT.parents[1];HF=ROOT/'vendor/HardFloat-1/source'
TASK=BASE/'native/audits/20261107/riscv_pe_contest/contest/task1'
case=ROOT/'work/fp32-test';case.mkdir(parents=True,exist_ok=True)
np.seterr(all='ignore');rng=np.random.default_rng(0x50453332)
def f(u):return np.array([u],np.uint32).view(np.float32)[0]
def bits(v):return int(np.array([v],np.float32).view(np.uint32)[0])
def expect(op,a,b):
    x,y=f(a),f(b)
    if op==0:v=np.float32(x+y)
    elif op==1:v=np.float32(x-y)
    elif op==2:v=np.float32(x*y)
    elif op==3:v=np.float32(x/y)
    elif op==4:v=np.sqrt(x,dtype=np.float32)
    elif op==5:v=np.float32(np.array([a],np.uint32).view(np.int32)[0])
    elif op in [6,8]:
        val=float(x)
        integer=2147483647 if np.isnan(x) or val>=2147483647 else -2147483648 if val<=-2147483648 else int(np.rint(val)) if op==6 else int(val)
        return integer&0xffffffff,0
    else:return (8 if np.isnan(x) or np.isnan(y) else 1 if x<y else 2 if x==y else 4),0
    return bits(v),int(np.isnan(v))
special=[0,0x80000000,1,0x80000001,0x007fffff,0x00800000,0x7f7fffff,0xff7fffff,0x3f800000,0xbf800000,0x3fc00000,0x40200000,0x4f000000,0xcf000000,0x7f800000,0xff800000,0x7fc00000,0x7f800001]
vectors=[]
for op in range(9):
    pairs=[(a,b) for a in special for b in special]+[(int(a),int(b)) for a,b in rng.integers(0,2**32,size=(2048,2),dtype=np.uint32)]
    for a,b in pairs:
        want,nan=expect(op,a,b);vectors.append((op,a,b,want,nan,0,0))
for op,a,b,flag in [(3,0x3f800000,0,8),(3,0,0,16),(4,0xbf800000,0,16),(0,0x3f800000,0x3f800000,0),(2,0x7f7fffff,0x40000000,5),(6,0x7fc00000,0,16)]:
    want,nan=expect(op,a,b);vectors.append((op,a,b,want,nan,31,flag))
for a,flag in [(0x3fc00000,1),(0xbfc00000,1),(0x3f800000,0),(0x7fc00000,16),(0xcf000000,0),(0x4f000000,16)]:
    want,nan=expect(8,a,0);vectors.append((8,a,0,want,nan,31,flag))
(case/'vectors.hex').write_text(''.join(' '.join('%08x'%v for v in row)+'\n' for row in vectors))
files=[HF/'HardFloat_primitives.v',HF/'HardFloat_rawFN.v',HF/'RISCV/HardFloat_specialize.v']+[HF/(n+'.v') for n in ['fNToRecFN','recFNToFN','isSigNaNRecFN','addRecFN','mulRecFN','divSqrtRecFN_small','iNToRecFN','recFNToIN','compareRecFN']]+[ROOT/'rtl/fp32_alu.v']
(ROOT/'hardfloat-files.json').write_text(json.dumps([str(p) for p in files],indent=2)+'\n')
def run(args,name):
    print('FP32_PHASE='+name,flush=True)
    with (case/(name+'.log')).open('w') as log:r=subprocess.run(list(map(str,args)),cwd=case,stdout=log,stderr=subprocess.STDOUT,timeout=900)
    if r.returncode:raise RuntimeError(name+' failed')
run(['/usr/bin/gcc','-fPIC','-c',TASK/'my_design/compat/pthread_yield_compat.c','-o',case/'compat.o'],'compat')
run(['/home/zhz/miniforge3/bin/python3',TASK/'scripts/vcs_vm.py','-full64','-sverilog','-timescale=1ns/1ps','+incdir+'+str(HF),'+incdir+'+str(HF/'RISCV'),*files,ROOT/'tests/tb_fp32.sv','-top','tb_fp32','-Mdir='+str(case/'csrc'),'-o',case/'simv','-LDFLAGS',case/'compat.o'],'compile')
run([case/'simv','+VECTORS='+str(case/'vectors.hex')],'simulate')
text=(case/'simulate.log').read_text();assert 'FP32_ALU_PASS vectors='+str(len(vectors)) in text and 'Fatal:' not in text,text[-1000:]
(case/'summary.json').write_text(json.dumps({'status':'PASS','vectors':len(vectors),'ops':['ADD','SUB','MUL','DIV','SQRT','I32_TO_F32','F32_TO_I32_RNE','CMP','F32_TO_I32_RTZ'],
    'rounding':'arithmetic RNE; float-to-int RNE or RTZ','checks':['random raw float bits','subnormals','signed zero','infinities','NaNs','selected exception flags','backpressure','reset while busy'],
    'scope':'shared FP32 ALU candidate; vector/DMA/SoC integration pending'},indent=2)+'\n')
print('FP32_CANDIDATE_PASS',flush=True)
