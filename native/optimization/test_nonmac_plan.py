"""Exhaustive C-lowered model address mapping; no image/model execution."""
import collections,hashlib,json,math,struct,subprocess
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parent;BASE=ROOT.parents[1];SWIN=BASE/'native/swin-tiny'
plan=ROOT/'work/fused-plan';case=ROOT/'work/nonmac-plan';case.mkdir(parents=True,exist_ok=True)
inputs=[Path(__file__),ROOT/'c/nonmac_plan.c',ROOT/'c/nonmac_plan.h',ROOT/'c/nonmac_plan_dump.c',ROOT/'c/aux_plan.c',ROOT/'c/aux_plan.h',SWIN/'c/graph.h',plan/'graph.bin',plan/'constants.bin',plan/'gelu-fusions.bin']
hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest()for p in inputs}
with(case/'compile.log').open('w')as log:subprocess.run(['gcc','-std=c11','-O2','-Wall','-Wextra','-Werror','-Wno-misleading-indentation','-I'+str(SWIN/'c'),'-I'+str(ROOT/'c'),ROOT/'c/nonmac_plan.c',ROOT/'c/aux_plan.c',ROOT/'c/nonmac_plan_dump.c','-o',case/'dump'],stdout=log,stderr=subprocess.STDOUT,check=True)
with(case/'dump.log').open('w')as log:subprocess.run([case/'dump',plan/'graph.bin',plan/'constants.bin',case/'commands.jsonl',plan/'gelu-fusions.bin'],stdout=log,stderr=subprocess.STDOUT,check=True)
raw=(plan/'graph.bin').read_bytes();constants=(plan/'constants.bin').read_bytes();h=struct.unpack_from('<16I',raw)
tensors=[struct.unpack_from('<6I8i',raw,h[7]+i*56)for i in range(h[2])];nodes=[struct.unpack_from('<4I8i16i4fi',raw,h[8]+i*132)for i in range(h[3])]
def shape(t):return tuple(t[6:6+t[3]])
def width(t):return 4 if t[2]in[1,6]else 8 if t[2]==7 else 1
def addr(t):assert t[4]in[0,1];return h[12+t[4]]+t[0]
def integers(id):
 t=tensors[id];assert t[4]==0 and t[2]in[6,7]
 return np.frombuffer(constants,dtype='<i8'if t[2]==7 else'<i4',count=t[1],offset=t[0]).tolist()
commands=[json.loads(x)for x in(case/'commands.jsonl').read_text().splitlines()];groups=collections.defaultdict(list)
for c in commands:groups[c['node'],c['post'],c['batch']].append(c)
fused=set()
for f in struct.iter_unpack('<7I',(plan/'gelu-fusions.bin').read_bytes()):fused.update(range(f[0],f[1]+1))
counts=collections.Counter();elements=collections.Counter();post_batches=0
def expanded(c,slot,stride_slot,step=4):
 a=c['args'];return(a[slot]+np.arange(a[5],dtype=np.int64)[:,None]*a[stride_slot]+np.arange(a[4],dtype=np.int64)[None,:]*step).ravel()
def param(c,slot,expected):assert c['patch_mask']&(1<<slot)and c['patch_address'][slot]==expected
def check_reorder(c,expected_source,expected_dest):
 a=c['args'];rank=a[3];dims=a[4:4+rank];strides=a[8:8+rank];rows=math.prod(dims);row=a[2];coords=np.indices(dims,dtype=np.int64).reshape(rank,-1)
 source=a[0]+sum(coords[k]*strides[k]for k in range(rank));step=4
 actual=(source[:,None]+np.arange(0,row,step,dtype=np.int64)[None,:]).ravel()
 np.testing.assert_array_equal(actual,expected_source);assert a[1]==expected_dest
 assert expected_dest+rows*row<=0x84000000
for i,n in enumerate(nodes):
 if i in fused or n[1]not in[1,3]:continue
 o=tensors[n[3]];op=n[0]
 if n[1]==3:
  j=struct.unpack_from('<12i',raw,h[9]+n[-1]*48);batches,m,nc,k=j[:4];aa=0x83000000;bb=(aa+m*k+63)&~63;cc=(bb+k*nc+63)&~63;dd=(cc+m*nc*4+63)&~63
  assert dd+m*nc*4<=0x83fff000
  for batch in range(batches):
   cs=groups.pop((i,1,batch));assert len(cs)==(2 if op==18 else 1);c=cs[0];a=c['args'];assert c['opcode']==7 and a[10]==(7 if n[2]>2 else 6)
   assert a[4:7]==[nc,m,nc*4]and a[9]==nc*4;np.testing.assert_array_equal(expanded(c,0,6),cc+np.arange(m*nc)*4)
   target=dd if op==18 else addr(o)+batch*m*nc*4;np.testing.assert_array_equal(expanded(c,3,9),target+np.arange(m*nc)*4)
   param(c,13,addr(tensors[j[5]]));bs=tensors[j[8]]
   if bs[1]==1:assert a[11]&1;param(c,14,addr(bs))
   else:np.testing.assert_array_equal(expanded(c,1,7),addr(bs)+np.tile(np.arange(nc)*4,m))
   if n[2]>2:np.testing.assert_array_equal(expanded(c,2,8),addr(tensors[n[6]])+np.tile(np.arange(nc)*4,m))
   if op==18:
    expected=dd+np.arange(m*nc,dtype=np.int64).reshape(m,nc).T.ravel()*4;check_reorder(cs[1],expected,addr(o))
   post_batches+=1;elements['POST']+=m*nc
  counts['POST_nodes']+=1;continue
 if op not in[5,6,8,9,10,11,13,14,15,17]or(op in[6,8,15]and o[2]!=1):continue
 cs=groups.pop((i,0,0));a=tensors[n[4]];counts[str(op)]+=1;elements[str(op)]+=o[1]
 if op in[9,10,11]:
  b=tensors[n[5]];actual_a=[];actual_b=[];actual_out=[]
  for c in cs:
   v=c['args'];assert c['opcode']==7 and v[10]==op-9;actual_a.append(expanded(c,0,6));actual_out.append(expanded(c,3,9))
   if v[11]&1:param(c,13,addr(b));actual_b.append(np.full(v[4]*v[5],addr(b),dtype=np.int64))
   else:actual_b.append(expanded(c,1,7))
  np.testing.assert_array_equal(np.concatenate(actual_a),addr(a)+np.broadcast_to(np.arange(a[1]).reshape(shape(a)),shape(o)).ravel()*4)
  np.testing.assert_array_equal(np.concatenate(actual_b),addr(b)+np.broadcast_to(np.arange(b[1]).reshape(shape(b)),shape(o)).ravel()*4)
  np.testing.assert_array_equal(np.concatenate(actual_out),addr(o)+np.arange(o[1])*4)
 elif op==5:
  assert len(cs)==1 and cs[0]['opcode']==8 and width(a)==4
  expected=addr(a)+np.arange(a[1],dtype=np.int64).reshape(shape(a)).transpose(n[12:12+o[3]]).ravel()*4;check_reorder(cs[0],expected,addr(o))
 elif op==6:
  assert len(cs)==1 and cs[0]['opcode']==8
  starts=integers(n[5]);ends=integers(n[6]);axes=integers(n[7])if n[2]>3 else list(range(len(starts)));steps=integers(n[8])if n[2]>4 else[1]*len(starts)
  slices=[slice(None)]*a[3]
  for start,end,axis,step in zip(starts,ends,axes,steps):slices[axis%a[3]]=slice(start,end,step)
  selected=np.arange(a[1],dtype=np.int64).reshape(shape(a))[tuple(slices)];assert selected.shape==shape(o)
  check_reorder(cs[0],addr(a)+selected.ravel()*4,addr(o))
 elif op==8:
  assert len(cs)==n[2];axis=n[12]%o[3]
  expected=np.concatenate([addr(tensors[t])+np.arange(tensors[t][1],dtype=np.int64).reshape(shape(tensors[t]))*4 for t in n[4:4+n[2]]],axis=axis).ravel();seen=np.zeros(o[1],dtype=bool)
  for c in cs:
   v=c['args'];assert c['opcode']==1 and not c['patch_mask']and v[2]%4==0
   actual=(v[0]+np.arange(v[3],dtype=np.int64)[:,None]*v[4]+np.arange(0,v[2],4)[None,:]).ravel()
   dest=(v[1]+np.arange(v[3],dtype=np.int64)[:,None]*v[5]+np.arange(0,v[2],4)[None,:]).ravel()
   assert np.all((dest-addr(o))%4==0)and np.all(np.diff(dest)>0);positions=(dest-addr(o))//4
   assert np.all(positions>=0)and np.all(positions<o[1])and not np.any(seen[positions]);seen[positions]=True
   np.testing.assert_array_equal(actual,expected[positions])
  assert np.all(seen)
 elif op==15:
  rows=o[1];inner=math.prod(shape(a)[2:]);assert rows*inner==a[1]and len(cs)==inner+2
  p0=0x83000000;p1=(p0+rows*4+63)&~63;transposed=(p1+rows*4+63)&~63
  expected=addr(a)+np.arange(a[1],dtype=np.int64).reshape(rows,inner).T.ravel()*4;check_reorder(cs[0],expected,transposed)
  previous=None
  for step,c in enumerate(cs[1:-1]):
   v=c['args'];destination=p1 if step%2 else p0;assert c['opcode']==7 and v[4:6]==[rows,1]and v[9]==rows*4 and v[10]==0 and not c['patch_mask']
   if not step:assert v[0]==transposed and v[11]==1 and v[13]==0
   else:assert v[0]==previous and v[1]==transposed+step*rows*4 and v[11]==0
   assert v[3]==destination;previous=destination
  c=cs[-1];v=c['args'];assert c['opcode']==7 and v[0]==previous and v[3]==addr(o)and v[4:6]==[rows,1]and v[10:12]==[2,1]and v[13]==struct.unpack('<I',struct.pack('<f',inner))[0]
 elif op in[13,14]:
  assert len(cs)==1;c=cs[0];v=c['args'];axis=n[12]%a[3];inner=math.prod(shape(a)[axis:]);rows=a[1]//inner
  assert v[:2]==[addr(a),addr(o)]
  if op==13:assert c['opcode']==6 and v[2:8]==[addr(tensors[n[5]]),addr(tensors[n[6]]),inner,rows,inner*4,inner*4]and v[8]==struct.unpack('<I',struct.pack('<f',n[28]))[0]
  else:assert c['opcode']==5 and axis==a[3]-1 and v[2:6]==[inner,rows,inner*4,inner*4]
 else:
  actual_a=[];actual_scale=[];actual_out=[];scale=tensors[n[5]];zero=tensors[n[6]]
  for c in cs:
   v=c['args'];assert c['opcode']==7 and v[10]==(3 if a[2]==3 else 4 if a[2]==2 else 5);actual_a.append(expanded(c,0,6,width(a)));actual_out.append(expanded(c,3,9))
   if scale[1]==1:param(c,13,addr(scale));assert v[11]&1 and v[12]==constants[zero[0]];actual_scale.append(np.full(v[4]*v[5],addr(scale),dtype=np.int64))
   else:assert a[2]==6 and not any(constants[zero[0]:zero[0]+zero[1]*width(zero)]);actual_scale.append(expanded(c,1,7))
  np.testing.assert_array_equal(np.concatenate(actual_a),addr(a)+np.arange(a[1])*width(a));np.testing.assert_array_equal(np.concatenate(actual_out),addr(o)+np.arange(o[1])*4)
  np.testing.assert_array_equal(np.concatenate(actual_scale),addr(scale)+(np.zeros(o[1],dtype=np.int64)if scale[1]==1 else np.arange(o[1])*4))
assert not groups and counts=={'9':77,'10':12,'5':62,'6':58,'8':23,'13':29,'14':12,'15':1,'17':67,'POST_nodes':77},(counts,list(groups))
for c in commands:
 a=c['args'];assert not(c['patch_mask']&~sum(1<<i for i in[13,14,15]))
 if c['opcode']==7:
  assert 0<a[4]<=65535 and a[5]>0 and a[9]>=a[4]*4
  dest=(a[3],a[3]+(a[5]-1)*a[9]+a[4]*4)
  sources=[(a[0],a[0]+(a[5]-1)*a[6]+a[4]*(1 if a[10]in[3,4]else 4))]
  if not(a[11]&1):sources.append((a[1],a[1]+(a[5]-1)*a[7]+a[4]*4))
  if a[10]==7 and not(a[11]&2):sources.append((a[2],a[2]+(a[5]-1)*a[8]+a[4]*4))
  for lo,hi in sources:assert 0x80000000<=lo<hi<=0x84000000 and max(lo,dest[0])>=min(hi,dest[1])
  assert 0x80000000<=dest[0]<dest[1]<=0x84000000
 elif c['opcode']==1:
  assert not c['patch_mask']and a[2]>0 and a[3]>0 and a[5]>=a[2]
  lo,hi=a[0],a[0]+(a[3]-1)*a[4]+a[2];dst,end=a[1],a[1]+(a[3]-1)*a[5]+a[2]
  assert 0x80000000<=lo<hi<=0x84000000 and 0x80000000<=dst<end<=0x84000000 and max(lo,dst)>=min(hi,end)
for p,want in hashes.items():assert hashlib.sha256(Path(p).read_bytes()).hexdigest()==want
summary={'status':'C_MODEL_COMMAND_ADDRESS_MAPPING_PASS','node_counts':dict(counts),'elements_checked':dict(elements),'POST_batch_commands':post_batches,'total_commands':len(commands),'commands_sha256':hashlib.sha256((case/'commands.jsonl').read_bytes()).hexdigest(),'source_sha256':hashes,
 'remaining_CPU_nonmac_nodes':[{'node':i,'opcode':n[0],'dtype':tensors[n[3]][2],'output_bytes':tensors[n[3]][1]*width(tensors[n[3]])}for i,n in enumerate(nodes)if i not in fused and n[1]==1 and n[0]not in[5,9,10,11,13,14,16,17]and tensors[n[3]][2]!=1],
 'scope':'actual C command generator exhaustively checked against independent NumPy tensor/broadcast/transpose/Slice/Concat/postscale indices and sequential AvgPool data/accumulator order; original scalar addresses and operand order; no model numeric inference or performance; CPU retains small integer shape metadata'}
(case/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary,indent=2),flush=True)
