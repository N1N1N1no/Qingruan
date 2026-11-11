"""Compare finite GELU tables against classic Newlib math, without adoption.

Only model parameters are used; baseline tables remain untouched. This checks
all finite raw/channel combinations against the source oracle, while actual
RV32 erff sampling and final organizer accuracy are separate evidence.
"""
import ctypes,hashlib,json,subprocess
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parent;BASE=ROOT.parents[1]
case=ROOT/'work/gelu-newlib-audit';case.mkdir(parents=True,exist_ok=True)
old=ROOT/'work/lookup-tables';source=BASE/'native/swin-tiny/work/verify.ylnpqxth'
plan=json.loads((source/'plan.json').read_text());constants=(source/'constants.bin').read_bytes()
wrapper=case/'wrap.c';wrapper.write_text('extern float reference_newlib_erff(float);\nfloat __wrap_erff(float x){return reference_newlib_erff(x);}\n')
files=[ROOT/'c/gelu_tables.c',ROOT/'work/erf-reference/reference.c',ROOT/'work/exp-reference/reference.c',wrapper]
subprocess.run(['/usr/bin/gcc','-O2','-fno-builtin','-ffp-contract=off','-fexcess-precision=standard','-shared','-fPIC',*map(str,files),'-Wl,--wrap=erff','-lm','-o',str(case/'gelu_tables.so')],check=True)
lib=ctypes.CDLL(str(case/'gelu_tables.so'));fn=lib.make_gelu_tables;ptr=ctypes.POINTER(ctypes.c_float)
fn.argtypes=[ctypes.c_void_p,ctypes.c_uint32,ptr,ctypes.c_float,ctypes.c_int32,ctypes.c_float,ctypes.c_float,ctypes.c_float,ctypes.c_float,ctypes.c_int32]
def const(tid):
 t=plan['tensors'][tid];assert t['storage']=='constant'
 return np.frombuffer(constants[t['offset']:t['offset']+t['bytes']],dtype=t['dtype']).copy().reshape(t['shape'])
original=(old/'gelu.bin').read_bytes();blob=bytearray();records=[];changed_total=0
for f in json.loads((old/'gelu-fusions.json').read_text()):
 dq,add,div,erf,plus,mul,half,q=plan['schedule'][f['start']:f['end']+1]
 bias=const(next(i for i in add['inputs']if i!=dq['outputs'][0]));channels=f['channels'];assert bias.shape==(channels,)
 values=[const(i).item()for i in[dq['inputs'][1],dq['inputs'][2],div['inputs'][1],plus['inputs'][1],half['inputs'][1],q['inputs'][1],q['inputs'][2]]]
 table=np.empty((channels,256),np.uint8);fn(table.ctypes.data,channels,bias.ctypes.data_as(ptr),*values)
 candidate=table.tobytes();expected=original[f['offset']:f['offset']+f['table_bytes']];assert len(candidate)==len(expected)
 indices=np.flatnonzero(np.frombuffer(candidate,np.uint8)!=np.frombuffer(expected,np.uint8));changed_total+=len(indices)
 records.append({'fusion_start':f['start'],'channels':channels,'domain_values':len(candidate),'changed_values':len(indices),
  'examples':[{'channel':int(i)//256,'raw':int(i)%256,'existing_raw':expected[int(i)],'Newlib_source_raw':candidate[int(i)]}for i in indices[:16]]})
 blob+=candidate
assert len(blob)==len(original)==4521984
(case/'newlib-gelu.bin').write_bytes(blob)
cpu_path=ROOT/'work/erf-cpu/summary.json';cpu=json.loads(cpu_path.read_text())if cpu_path.exists()else None
summary={'status':'TABLES_IDENTICAL_AGAINST_NEWLIB_SOURCE'if changed_total==0 else'MATH_LIBRARY_TABLE_DIFFERENCES_REQUIRE_REVIEW',
 'domain_values':len(blob),'changed_values':int(changed_total),'fusions':records,'installed_RV32_erff_sample_test_passed':bool(cpu and cpu['status']=='PASS'),
 'baseline_table_unchanged':hashlib.sha256((old/'gelu.bin').read_bytes()).hexdigest()==hashlib.sha256(original).hexdigest(),
 'source_sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest()for p in files+[old/'gelu.bin',old/'gelu-fusions.json',source/'constants.bin']},
 'scope':'all finite model raw/channel GELU domains against the classic Newlib source oracle; RV32 samples do not prove exhaustive CPU execution; no table adoption, images/labels, end-to-end inference or organizer accuracy claim'}
(case/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps({k:summary[k]for k in['status','domain_values','changed_values','installed_RV32_erff_sample_test_passed','baseline_table_unchanged']}),flush=True)
