"""All model raw input addresses vs independent NumPy broadcast/im2col maps."""
import collections,hashlib,json,math,struct,subprocess
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parent;BASE=ROOT.parents[1];SWIN=BASE/'native/swin-tiny';plan=ROOT/'work/fused-plan';case=ROOT/'work/mac-plan';case.mkdir(parents=True,exist_ok=True)
inputs=[Path(__file__),ROOT/'c/mac_plan.c',ROOT/'c/mac_plan.h',ROOT/'c/mac_plan_dump.c',ROOT/'c/nonmac_plan.h',SWIN/'c/graph.h',plan/'graph.bin',plan/'constants.bin'];hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest()for p in inputs}
with(case/'compile.log').open('w')as f:subprocess.run(['gcc','-std=c11','-O2','-Wall','-Wextra','-Werror','-Wno-misleading-indentation','-I'+str(ROOT/'c'),'-I'+str(SWIN/'c'),ROOT/'c/mac_plan.c',ROOT/'c/mac_plan_dump.c','-o',case/'dump'],stdout=f,stderr=subprocess.STDOUT,check=True)
with(case/'dump.log').open('w')as f:subprocess.run([case/'dump',plan/'graph.bin',plan/'constants.bin',case/'commands.jsonl'],stdout=f,stderr=subprocess.STDOUT,check=True)
raw=(plan/'graph.bin').read_bytes();constants=(plan/'constants.bin').read_bytes();h=struct.unpack_from('<16I',raw)
ts=[struct.unpack_from('<6I8i',raw,h[7]+i*56)for i in range(h[2])];nodes=[struct.unpack_from('<4I8i16i4fi',raw,h[8]+i*132)for i in range(h[3])]
def shape(t):return tuple(t[6:6+t[3]])
def addr(t):assert t[4]in[0,1];return h[12+t[4]]+t[0]
def indices(t):return np.arange(t[1],dtype=np.int64).reshape(shape(t))
commands=[json.loads(x)for x in(case/'commands.jsonl').read_text().splitlines()];groups=collections.defaultdict(list)
for c in commands:groups[c['node'],c['batch']].append(c)
node_count=batch_count=im2col_count=checked_a=checked_b=direct_a=0;workspace=0x83000000
for i,n in enumerate(nodes):
 if n[1]!=3:continue
 node_count+=1;j=struct.unpack_from('<12i',raw,h[9]+n[-1]*48);batches,m,nc,k=j[:4];a,b,o=ts[j[4]],ts[j[7]],ts[n[3]];assert a[2]==b[2]==3
 # Postscale writes each completed batch before the next raw input is read.
 # Its complete output allocation must therefore preserve all raw batches.
 for t in[a,b]:assert max(addr(o),addr(t))>=min(addr(o)+o[1]*4,addr(t)+t[1])
 az,bz=ts[j[6]],ts[j[9]];assert az[4]==bz[4]==0
 for z in[az,bz]:assert len(set(constants[z[0]:z[0]+z[1]]))==1 and z[2]==3
 za=constants[az[0]];zb=constants[bz[0]]
 if n[0]==18:
  c,ih,iw=shape(a)[1:];kh,kw=shape(b)[2:];sy,sx=n[12:14];dy,dx=n[18:20];assert dy==dx==1 and not any(n[14:18])
  window=np.lib.stride_tricks.sliding_window_view(indices(a)[0],(kh,kw),axis=(1,2))[:,::sy,::sx,:,:]
  expected_im2col=addr(a)+window.transpose(1,2,0,3,4).ravel();assert len(expected_im2col)==m*k
  source_b=addr(b)+indices(b).reshape(nc,k).T.ravel()
 else:
  prefix=shape(o)[:-2]
  if batches==1:
   # The frozen compiler folds all leading A rows into M when B is shared.
   source_a=indices(a).ravel()[:m*k].reshape(1,m,k)
   raw_b=indices(b).ravel()[:k*nc].reshape((1,nc,k)if j[11]else(1,k,nc))
  else:
   assert math.prod(prefix)==batches
   source_a=np.broadcast_to(indices(a),prefix+(m,k)).reshape(batches,m,k)
   raw_b=np.broadcast_to(indices(b),prefix+((nc,k)if j[11]else(k,nc)))
  source_b_all=(raw_b.swapaxes(-1,-2)if j[11]else raw_b).reshape(batches,k,nc)
 for batch in range(batches):
  cs=groups.pop((i,batch));assert len(cs)==(2 if n[0]==18 else 1)
  if n[0]==18:
   r=cs[0];v=r['args'];assert r['opcode']==8 and v[1]==workspace and v[3]==4
   dims=v[4:8];strides=v[8:12];coords=np.indices(dims,dtype=np.int64).reshape(4,-1)
   source=v[0]+sum(coords[d]*strides[d]for d in range(4));actual=(source[:,None]+np.arange(v[2])[None,:]).ravel();np.testing.assert_array_equal(actual,expected_im2col)
   im2col_count+=1;cc=(workspace+m*k+63)&~63
  else:cc=workspace
  cmd=cs[-1];v=cmd['args'];assert cmd['opcode']==3 and v[2:9]==[cc,m,nc,k,za,zb,1 if n[0]==18 else j[11]]
  actual_a=v[0]+np.arange(m*k,dtype=np.int64)
  if n[0]==18:np.testing.assert_array_equal(actual_a,workspace+np.arange(m*k))
  else:np.testing.assert_array_equal(actual_a,addr(a)+source_a[batch].ravel());direct_a+=m*k
  actual_b=v[1]+(np.arange(k,dtype=np.int64)[:,None]+np.arange(nc,dtype=np.int64)[None,:]*k if v[8]else np.arange(k*nc,dtype=np.int64).reshape(k,nc))
  expected_b=source_b if n[0]==18 else addr(b)+source_b_all[batch].ravel();np.testing.assert_array_equal(actual_b.ravel(),expected_b)
  dd=(cc+m*nc*4+63)&~63;assert dd+m*nc*4<=0x83fff000 and max(v[0],cc)>=min(v[0]+m*k,cc+m*nc*4) and max(v[1],cc)>=min(v[1]+k*nc,cc+m*nc*4)
  batch_count+=1;checked_a+=m*k;checked_b+=k*nc
assert not groups and(node_count,batch_count,im2col_count)==(77,1877,1)
for p,want in hashes.items():assert hashlib.sha256(Path(p).read_bytes()).hexdigest()==want
summary={'status':'ALL_MODEL_MAC_RAW_BINDING_ADDRESS_PASS','MAC_nodes':node_count,'MAC_batches':batch_count,'hardware_im2col_commands':im2col_count,'A_addresses_checked':checked_a,'B_addresses_checked':checked_b,'direct_A_elements':direct_a,'direct_B_elements':checked_b,'previous_CPU_packing_bytes_removed':checked_a+checked_b,'source_sha256':hashes,
 'scope':'C raw tensor address commands compared exhaustively with independent NumPy broadcast/transpose/im2col; all77 MAC nodes/1877 batches, no image/model numeric inference or latency measurement; actual RV32 integration and firmware hook pending'}
(case/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary,indent=2),flush=True)
