"""Make contiguous shape operations aliases and recompute physical lifetimes.

Transpose and noncontiguous Slice/Concat remain materialized here. No assumption
that an arbitrary permuted tensor can be reshaped without a copy is introduced.
"""
import copy,collections,json,math,shutil,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent;BASE=ROOT.parents[1]
sys.path[:0]=[str(BASE/'native/swin-tiny'),str(BASE/'native/swin-tiny/runtime')]
from swin.compile import emit
import numpy as np
source=BASE/'native/swin-tiny/work/verify.ylnpqxth';plan=json.loads((source/'plan.json').read_text())
tensors=plan['tensors'];nodes=plan['schedule'];constants=(source/'constants.bin').read_bytes()
case=ROOT/'work/view-plan';case.mkdir(parents=True,exist_ok=True)
aliases={};counts=collections.Counter()
def const(i):
 t=tensors[i]
 if t['storage']!='constant':return None
 return np.frombuffer(constants[t['offset']:t['offset']+t['bytes']],dtype=t['dtype']).reshape(t['shape'])
for n in nodes:
 offset=0
 if n['op'] in ['Reshape','Flatten','Identity']:
  src=n['inputs'][0];dst=n['outputs'][0]
  assert tensors[src]['bytes']==tensors[dst]['bytes'] and tensors[src]['dtype']==tensors[dst]['dtype']
 elif n['op']=='Gather' and n['attributes'].get('axis',0)==0:
  src=n['inputs'][0];dst=n['outputs'][0];index=const(n['inputs'][1])
  if index is None or index.shape!=():continue
  position=int(index);shape=tensors[src]['shape'];position=position+shape[0] if position<0 else position
  if not 0<=position<shape[0] or tensors[src]['bytes']!=tensors[dst]['bytes']*shape[0]:continue
  offset=position*tensors[dst]['bytes']
 else:continue
 assert tensors[src]['storage']!='quantized_view'
 if src in aliases:
  owner,base_offset=aliases[src];offset+=base_offset
 else:owner=src
 aliases[dst]=(owner,offset);n['executor']='quantized_view';n['view_lowering']='contiguous_alias';counts[n['op']]+=1

def physical(i):
 if i in aliases:return aliases[i][0]
 if tensors[i]['storage']=='quantized_view':return physical(tensors[i]['raw_tensor'])
 return i
last={i:-1 for i in range(len(tensors))};birth={}
for n in nodes:
 for i in n['outputs']:birth[i]=n['node_index']
 for i in n['inputs']:last[physical(i)]=max(last[physical(i)],n['node_index'])
 if n['executor']=='pe':
  for p in ['A','B']:
   i=n['job'][p]['tensor_id'];last[physical(i)]=max(last[physical(i)],n['node_index'])
for i,t in enumerate(tensors):
 if t['name']=='output':last[physical(i)]=len(nodes)
 if physical(i)==i:last[i]=max(last[i],birth.get(i,-1))
free=[];active={};arena=0;peak=0;allocations=[]
def align(n):return (n+63)//64*64
def allocate(i):
 global arena,peak
 t=tensors[i]
 if physical(i)!=i or t['storage']!='activation':return
 size=align(max(t['bytes'],1));free.sort()
 for j,(offset,length) in enumerate(free):
  if length>=size:
   free.pop(j)
   if length>size:free.append((offset+size,length-size))
   break
 else:offset=arena;arena+=size
 t['offset']=offset;active[i]=(offset,size);peak=max(peak,sum(x[1] for x in active.values()));allocations.append(i)
for i,t in enumerate(tensors):
 if t['name']=='input':allocate(i)
for n in nodes:
 for i in n['outputs']:allocate(i)
 for i,(offset,size) in list(active.items()):
  if last[i]<=n['node_index']:free.append((offset,size));del active[i]
 merged=[]
 for offset,size in sorted(free):
  if merged and merged[-1][0]+merged[-1][1]==offset:
   a,b=merged[-1];merged[-1]=(a,b+size)
  else:merged.append((offset,size))
 free=merged
for i,(owner,offset) in aliases.items():
 t=tensors[i];base=tensors[owner];assert offset+t['bytes']<=base['bytes']
 t.update(storage=base['storage'],offset=base['offset']+offset,view_of=owner,view_byte_offset=offset)
for i,t in enumerate(tensors):t['last_use']=last[physical(i)]
for ix,i in enumerate(allocations):
 a=tensors[i]
 for j in allocations[ix+1:]:
  b=tensors[j]
  live=max(birth.get(i,-1),birth.get(j,-1))<=min(last[i],last[j])
  overlap=max(a['offset'],b['offset'])<min(a['offset']+align(a['bytes']),b['offset']+align(b['bytes']))
  assert not(live and overlap),(i,j)
old_arena=plan['memory']['activation_arena_bytes'];plan['memory'].update(activation_arena_bytes=arena,peak_live_activation_bytes=peak)
plan['coverage']=dict(collections.Counter(n['executor'] for n in nodes));plan['view_optimization']={'aliases':len(aliases),'operators':dict(counts),'old_arena_bytes':old_arena,'physical_allocations':len(allocations),'copied_bytes_removed':sum(tensors[i]['bytes'] for i in aliases)}
plan['scope']='complete graph with contiguous aliases; transpose/noncontiguous operations preserved; full functional proof pending'
shutil.copyfile(source/'constants.bin',case/'constants.bin');(case/'plan.json').write_text(json.dumps(plan,indent=2)+'\n');compiled=emit(plan,case)
summary={'status':'PLAN_AND_LIFETIME_PASS_EXECUTION_PENDING','view_optimization':plan['view_optimization'],'activation_arena_bytes':arena,'peak_live_bytes':peak,'compiled':compiled,'physical_overlap_pairs_checked':len(allocations)*(len(allocations)-1)//2}
(case/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary),flush=True)
