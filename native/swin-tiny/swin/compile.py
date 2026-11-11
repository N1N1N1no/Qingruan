"""Serialize the fixed graph into a small RV32-readable, little-endian image."""
import json
import struct
from pathlib import Path
from .model import sha

OPS = ['Constant','Identity','Reshape','Flatten','Shape','Transpose','Slice','Gather',
       'Concat','Add','Mul','Div','Erf','LayerNormalization','Softmax',
       'GlobalAveragePool','QuantizeLinear','DequantizeLinear','Conv','MatMul','Gemm']
DTYPES = {'float32':1,'int8':3,'uint8':2,'int32':6,'int64':7}
STORAGE = {'constant':0,'activation':1,'quantized_view':2}
EXEC = {'static':0,'riscv':1,'quantized_view':2,'pe':3}

def emit(plan, destination):
    destination=Path(destination)
    tensors=bytearray(); nodes=bytearray(); jobs=bytearray(); job_count=0
    names={t['name']:t['id'] for t in plan['tensors']}
    for t in plan['tensors']:
        dims=t['shape']; assert len(dims)<=8
        count=1
        for d in dims:count*=d
        tensors += struct.pack('<6I8i',t.get('offset',0),count,DTYPES[t['dtype']],
                               len(dims),STORAGE[t['storage']],t.get('raw_tensor',0),
                               *(dims+[1]*(8-len(dims))))
    for n in plan['schedule']:
        a=n['attributes']; ix=[0]*16; fx=[0.0]*4
        ix[0]=a.get('axis',1 if n['op'] in ['QuantizeLinear','DequantizeLinear','Flatten'] else -1)
        if n['op']=='Transpose':ix[:len(a['perm'])]=a['perm']
        if n['op']=='Conv':
            ix[:8]=a.get('strides',[1,1])+a.get('pads',[0]*4)+a.get('dilations',[1,1])
        fx[0]=a.get('epsilon',1e-5)
        ins=n['inputs']; assert len(ins)<=8
        job_id=-1
        if n['executor']=='pe':
            j=n['job']; job_id=job_count; job_count+=1
            values=[j[k] for k in ['batch','M','N','K']]
            values += [j[p][f+'_id'] for p in ['A','B'] for f in ['tensor','scale','zero_point']]
            values += [j['B']['axis'],a.get('transB',0)]
            jobs += struct.pack('<12i',*values)
        nodes += struct.pack('<4I8i16i4fi',OPS.index(n['op']),EXEC[n['executor']],len(ins),n['outputs'][0],
                             *(ins+[-1]*(8-len(ins))),*ix,*fx,job_id)
    # Fixed-size header; all table offsets are relative to descriptor base.
    header_bytes=64; tensor_off=header_bytes; node_off=tensor_off+len(tensors); job_off=node_off+len(nodes)
    values=[0x4e495753,1,len(plan['tensors']),len(plan['schedule']),job_count,names['input'],names['output'],
            tensor_off,node_off,job_off,plan['memory']['constant_bytes'],plan['memory']['activation_arena_bytes'],
            plan['memory']['constant_base'],plan['memory']['activation_base'],0,0]
    image=struct.pack('<16I',*values)+tensors+nodes+jobs
    assert len(image)<=plan['memory']['descriptor_capacity']
    path=destination/'graph.bin';path.write_bytes(image)
    manifest={'schema':'SWIN_GRAPH_V1','bytes':len(image),'sha256':sha(path),
              'tensor_stride':56,'node_stride':132,'job_stride':48,'tensors':len(plan['tensors']),
              'nodes':len(plan['schedule']),'jobs':job_count,'model_sha256':plan['model_sha256'],
              'constant_sha256':plan['memory']['constant_blob_sha256']}
    (destination/'graph_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest
