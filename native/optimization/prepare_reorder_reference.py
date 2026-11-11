"""Prove folded DMA descriptions match all active transpose index maps."""
import hashlib,json,math,struct
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parent;plan=ROOT/'work/fused-plan';case=ROOT/'work/reorder-reference';case.mkdir(parents=True,exist_ok=True)
raw=(plan/'graph.bin').read_bytes();h=struct.unpack_from('<16I',raw)
ts=[struct.unpack_from('<6I8i',raw,h[7]+i*56)for i in range(h[2])]
records=[];verified=0
for index in range(h[3]):
 n=struct.unpack_from('<4I8i16i4fi',raw,h[8]+index*132)
 if n[0]!=5 or n[1]!=1:continue
 a,o=ts[n[4]],ts[n[3]];dims=list(o[6:6+o[3]]);adims=list(a[6:6+a[3]]);perm=list(n[12:12+o[3]])
 width=4 if a[2]in[1,6]else 8 if a[2]==7 else 1
 assert sorted(perm)==list(range(a[3]))and dims==[adims[j]for j in perm]and a[1]==o[1]
 strides=[0]*a[3];span=width
 for j in range(a[3]-1,-1,-1):strides[j]=span;span*=adims[j]
 perm_strides=[strides[j]for j in perm];row_bytes=width;rank=o[3]
 while rank and(dims[rank-1]==1 or perm_strides[rank-1]==row_bytes):row_bytes*=dims[rank-1];rank-=1
 outer=dims[:rank]if rank else[1];source_strides=perm_strides[:rank]if rank else[0];rank=max(1,rank);assert rank<=4
 # Independent dense transpose of input element indices, compared against
 # every address produced by the folded row description (not image data).
 expected=np.arange(a[1],dtype=np.uint32).reshape(adims).transpose(perm).reshape(-1).astype(np.uint64)*width
 byte_index=np.arange(o[1],dtype=np.uint64)*width;row_index=byte_index//row_bytes;actual=byte_index%row_bytes
 for dim,stride in reversed(list(zip(outer,source_strides))):actual+=(row_index%dim)*stride;row_index//=dim
 assert not np.any(row_index)and np.array_equal(actual,expected),index
 verified+=o[1]
 records.append({'node':index,'rank':rank,'row_bytes':row_bytes,'dimensions':outer,'source_strides':source_strides,
  'rows':math.prod(outer),'source_bytes':span,'output_bytes':o[1]*width,'positions_proved':o[1],'original_shape':adims,'permutation':perm})
assert len(records)==62
payload=[]
for x in records:payload.append(' '.join(map(str,[x['node'],x['rank'],x['row_bytes'],*(x['dimensions']+[0]*(4-x['rank'])),*(x['source_strides']+[0]*(4-x['rank'])),x['output_bytes']]))+'\n')
(case/'vectors.txt').write_text(''.join(payload))
summary={'status':'ALL62_TRANSPOSE_INDEX_MAPS_PROVED_DMA_RTL_PENDING','nodes':62,'element_positions':verified,'descriptions':records,
 'graph_sha256':hashlib.sha256(raw).hexdigest(),'scope':'all active transpose index maps versus independent dense NumPy transpose; no model inference or system performance claim'}
(case/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print('REORDER_DESCRIPTIONS_PROVED nodes=62 elements='+str(verified),flush=True)
