import json,struct,subprocess
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parent;BASE=ROOT.parents[1];TASK=BASE/'native/audits/20261107/riscv_pe_contest/contest/task1'
src=ROOT/'work/lookup-tables';info=json.loads((src/'quant-thresholds.json').read_text());data=(src/'quant-thresholds.bin').read_bytes()
case=ROOT/'work/quant-test';case.mkdir(parents=True,exist_ok=True);tables=[];vectors=[];checked=0
np.seterr(all='ignore');rng=np.random.default_rng(0x5152544c)
for q in info:
 assert q['dtype']=='int8'
 heap=np.frombuffer(data[q['threshold_offset']:q['threshold_offset']+1020],'<u4');tables.extend(int(v) for v in heap)
 boundaries=np.sort(heap);keys=np.concatenate([(boundaries.astype(np.int64)[:,None]+[-1,0,1]).ravel().astype(np.uint32),rng.integers(0x007fffff,0xff800001,size=256,dtype=np.uint32)])
 bits=np.where(keys&0x80000000,keys^np.uint32(0x80000000),~keys).astype(np.uint32)
 bits=np.concatenate([bits,np.array([0,0x80000000,0x7f800000,0xff800000,0x7fc00000],np.uint32)])
 values=bits.view(np.float32);expected=np.clip(np.rint(values/np.float32(q['scale']))+q['zero'],-128,127)
 vectors.append(str(len(bits))+'\n')
 for raw,x,want in zip(bits,values,expected):
  bad=int(np.isnan(x));answer=0 if bad else int(want)&255;vectors.append('%08x %02x %01x\n'%(raw,answer,bad))
 checked+=len(bits)
(case/'tables.hex').write_text(''.join('%08x\n'%v for v in tables));(case/'vectors.txt').write_text(''.join(vectors))
def run(args,name):
 print('QUANT_TEST_PHASE='+name,flush=True)
 with (case/(name+'.log')).open('w') as f:r=subprocess.run(list(map(str,args)),cwd=case,stdout=f,stderr=subprocess.STDOUT,timeout=900)
 if r.returncode:raise RuntimeError(name+' failed')
run(['/usr/bin/gcc','-fPIC','-c',TASK/'my_design/compat/pthread_yield_compat.c','-o',case/'compat.o'],'compat')
run(['/home/zhz/miniforge3/bin/python3',TASK/'scripts/vcs_vm.py','-full64','-sverilog','-timescale=1ns/1ps',ROOT/'rtl/quant_lookup8.v',ROOT/'tests/tb_quant_lookup.sv','-top','tb_quant_lookup','-Mdir='+str(case/'csrc'),'-o',case/'simv','-LDFLAGS',case/'compat.o'],'compile')
run([case/'simv','+TABLES='+str(case/'tables.hex'),'+VECTORS='+str(case/'vectors.txt')],'simulate')
text=(case/'simulate.log').read_text();assert 'QUANT_LOOKUP_PASS tables=178 checked='+str(checked) in text and 'Fatal:' not in text,text[-2000:]
(case/'summary.json').write_text(json.dumps({'status':'PASS','tables':len(info),'RTL_vectors':checked,'pipeline_stages':8,
 'throughput':'one input per cycle without output backpressure','checks':['all model quantizers','transition +/- 1 ULP','random float bits','signed zero','infinities','NaN rejection','input bubbles','output backpressure'],
 'scope':'standalone quantizer candidate; final shared-memory and AXI integration pending'},indent=2)+'\n');print('QUANT_LOOKUP_RTL_PASS',flush=True)
