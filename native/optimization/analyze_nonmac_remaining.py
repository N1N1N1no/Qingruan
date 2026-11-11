"""Extract exact active non-MAC shapes/parameters for DMA architecture choices."""
import collections,hashlib,json,math,struct
from pathlib import Path
ROOT=Path(__file__).resolve().parent;plan=ROOT/'work/fused-plan'
raw=(plan/'graph.bin').read_bytes();constants=(plan/'constants.bin').read_bytes();h=struct.unpack_from('<16I',raw)
ts=[struct.unpack_from('<6I8i',raw,h[7]+i*56)for i in range(h[2])]
nodes=[struct.unpack_from('<4I8i16i4fi',raw,h[8]+i*132)for i in range(h[3])]
fusions=struct.iter_unpack('<7I',(plan/'gelu-fusions.bin').read_bytes());fused=set()
for f in fusions:fused.update(range(f[0],f[1]+1))
def shape(t):return list(t[6:6+t[3]])
def width(t):return 4 if t[2]in[1,6]else 8 if t[2]==7 else 1
def ref(t):return{'elements':t[1],'dtype':t[2],'shape':shape(t),'constant':t[4]==0}
items=[]
for i,n in enumerate(nodes):
 if i in fused or n[1]not in[1,3]:continue
 op=n[0];out=ts[n[3]];a=ts[n[4]]if n[2]else None
 if op not in[5,9,10,11,13,14,17,18,19,20]:continue
 item={'node':i,'op':op,'output':ref(out)}
 if a:item['input']=ref(a)
 if op in[9,10,11]:
  b=ts[n[5]];item['input_B']=ref(b)
  item['A_full_shape']=shape(a)==shape(out);item['B_full_shape']=shape(b)==shape(out);item['B_scalar']=b[1]==1
  padded=[1]*(out[3]-b[3])+shape(b)
  item['B_broadcast_axes']=[j for j,(x,y)in enumerate(zip(shape(out),padded))if y==1 and x!=1]
 if op==17:
  scale,zero=ts[n[5]],ts[n[6]];item['scale']=ref(scale);item['zero']=ref(zero)
  item['zero_all_zero']=zero[4]==0 and not any(constants[zero[0]:zero[0]+zero[1]*width(zero)])
 if op in[13,14]:
  axis=n[12];axis=axis+a[3]if axis<0 else axis
  item['axis']=axis;item['normalized_elements']=math.prod(shape(a)[axis:])if op==13 else shape(a)[axis]
  item['last_axis']=axis==a[3]-1
 if op==5:
  dims=shape(out);permutation=list(n[12:12+out[3]]);source_dims=shape(a);strides=[0]*a[3];s=width(a)
  for j in range(a[3]-1,-1,-1):strides[j]=s;s*=source_dims[j]
  permuted=[strides[j]for j in permutation];row_bytes=width(a);rank=out[3]
  while rank and(dims[rank-1]==1 or permuted[rank-1]==row_bytes):row_bytes*=dims[rank-1];rank-=1
  item['reorder_descriptor']={'rank':max(1,rank),'dimensions':dims[:rank]if rank else[1],'source_strides':permuted[:rank]if rank else[0],
   'contiguous_row_bytes':row_bytes,'rows':math.prod(dims[:rank]),'source_bytes':a[1]*width(a)}
 if n[1]==3:
  j=struct.unpack_from('<12i',raw,h[9]+n[-1]*48);item['postscale']={'A_scale':ref(ts[j[5]]),'B_scale':ref(ts[j[8]]),'bias':ref(ts[n[6]])if n[2]>2 else None}
 items.append(item)
counts=dict(sorted(collections.Counter(x['op']for x in items).items()));dq=[x for x in items if x['op']==17];binary=[x for x in items if x['op']in[9,10,11]]
summary={'status':'ARCHITECTURE_INPUTS_NOT_IMPLEMENTATION_PASS','counts_by_graph_opcode':counts,'S32_DQ':[x for x in dq if x['input']['dtype']==6],
 'binary_A_broadcast_nodes':[x['node']for x in binary if not x['A_full_shape']],
 'LayerNorm_widths':sorted(set(x['normalized_elements']for x in items if x['op']==13)),
 'max_reorder_descriptor_rank':max(x['reorder_descriptor']['rank']for x in items if x['op']==5),'nodes':items,
 'source_sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest()for p in[plan/'graph.bin',plan/'constants.bin',plan/'gelu-fusions.bin']},
 'scope':'active nodes in the frozen fused plan, used to size and compare module DMA/broadcast/reorder choices; no end-to-end inference or performance verification'}
(ROOT/'nonmac-architecture-inputs.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps({k:v for k,v in summary.items()if k not in['nodes','source_sha256','S32_DQ']}),flush=True)
