"""Complete static graph schedule, exact affine-INT8 PE jobs and memory layout."""
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import numpy as np
import onnx
from .model import Model, attrs, sha

ALIGN=64
def align(n):return (int(n)+ALIGN-1)//ALIGN*ALIGN


def build(model, destination):
    destination=Path(destination)
    macs=model.mapping()
    mac_by_index={n['node_index']:n for n in macs}
    names=list(model.initializers)
    names += [v.name for v in model.model.graph.input if v.name not in names]
    names += [o for n in model.nodes for o in n.output if o and o not in names]
    assert len(names)==len(set(names))
    ids={name:i for i,name in enumerate(names)}
    consumers=defaultdict(list)
    producer={}
    for i,node in enumerate(model.nodes):
        for slot,name in enumerate(node.input):
            if name:consumers[name].append((i,slot))
        for name in node.output:
            if name:producer[name]=i
    fused={}
    for i,node in enumerate(model.nodes):
        if node.op_type=='DequantizeLinear' and consumers[node.output[0]] and all(
            j in mac_by_index and slot<2 for j,slot in consumers[node.output[0]]):
            fused[node.output[0]]=node.input[0]
            consumers[node.input[0]] += [(j,slot) for j,slot in consumers[node.output[0]]]
    last={name:max([producer.get(name,-1)]+[j for j,_ in consumers[name]]) for name in names}
    for v in model.model.graph.output:last[v.name]=len(model.nodes)
    tensors=[]
    weights=destination/'constants.bin'
    with weights.open('wb') as stream:
        for name in names:
            shape=model.shapes[name]
            dtype=np.dtype(onnx.helper.tensor_dtype_to_np_dtype(model.dtypes[name]))
            entry={'id':ids[name],'name':name,'shape':shape,'dtype':str(dtype),
                   'bytes':math.prod(shape)*dtype.itemsize,'last_use':last[name]}
            if name in model.constants:
                position=align(stream.tell());stream.write(b'\0'*(position-stream.tell()))
                data=np.ascontiguousarray(model.constants[name]).astype(dtype.newbyteorder('<'),copy=False)
                stream.write(data.tobytes())
                entry.update(storage='constant',offset=position)
            elif name in fused:
                entry.update(storage='quantized_view',raw_tensor=ids[fused[name]])
            else:entry.update(storage='activation')
            tensors.append(entry)
    byname={t['name']:t for t in tensors}
    free=[];active={};arena_size=0;peak_live=0
    def allocate(name):
        nonlocal arena_size,peak_live
        t=byname[name]
        if t['storage']!='activation':return
        size=align(max(t['bytes'],1))
        free.sort()
        for index,(offset,length) in enumerate(free):
            if length>=size:
                free.pop(index)
                if length>size:free.append((offset+size,length-size))
                break
        else:offset=arena_size;arena_size+=size
        t['offset']=offset;active[name]=(offset,size)
        peak_live=max(peak_live,sum(n for _,n in active.values()))
    for v in model.model.graph.input:allocate(v.name)
    schedule=[]
    for i,node in enumerate(model.nodes):
        for name in node.output:
            if name:allocate(name)
        record={'node_index':i,'name':node.name,'op':node.op_type,
                'inputs':[ids[n] for n in node.input if n],
                'outputs':[ids[n] for n in node.output if n]}
        if node.op_type=='Constant':record.update(executor='static',attributes={})
        elif node.output[0] in fused:record.update(executor='quantized_view',attributes=attrs(node))
        elif i in mac_by_index:
            job=mac_by_index[i].copy()
            job['A']=job['A'].copy();job['B']=job['B'].copy()
            for operand in ['A','B']:
                for field in ['tensor','scale','zero_point']:
                    job[operand][field+'_id']=ids[job[operand][field]]
            record.update(executor='pe',attributes=attrs(node),job=job)
        else:
            attribute=attrs(node)
            if any(isinstance(v,bytes) for v in attribute.values()):
                attribute={k:v.decode() if isinstance(v,bytes) else v for k,v in attribute.items()}
            record.update(executor='riscv',attributes=attribute)
        schedule.append(record)
        for name,(offset,size) in list(active.items()):
            if last[name]<=i:
                free.append((offset,size));del active[name]
        # Coalesce adjacent blocks so layout does not grow due to fragmentation.
        merged=[]
        for offset,size in sorted(free):
            if merged and merged[-1][0]+merged[-1][1]==offset:
                previous,length=merged[-1];merged[-1]=(previous,length+size)
            else:merged.append((offset,size))
        free=merged
    constant_bytes=weights.stat().st_size
    # External AXI memory is explicit; the existing 256 KiB firmware area is retained.
    constant_base=0x80100000
    activation_base=align(constant_base+constant_bytes)
    required_end=activation_base+arena_size
    external_bytes=1 << max(26,(required_end-0x80000000-1).bit_length())
    if 0x80000000+external_bytes>0x90000000:
        raise ValueError('External-memory layout overlaps the PE MMIO aperture')
    plan={'schema':'SWIN_PLAN_V1','model_sha256':sha(model.path),'model':str(model.path),
          'schedule':schedule,'tensors':tensors,'all_nodes_scheduled':len(schedule)==len(model.nodes),
          'coverage':dict(Counter(n['executor'] for n in schedule)),
          'memory':{'firmware_base':0x80000000,'firmware_bytes':262144,
                    'descriptor_base':0x80040000,'descriptor_capacity':0xC0000,
                    'constant_base':constant_base,'constant_bytes':constant_bytes,
                    'activation_base':activation_base,'activation_arena_bytes':arena_size,
                    'peak_live_activation_bytes':peak_live,'external_axi_ram_bytes':external_bytes,
                    'external_ram_is_required':True,'ram_256KiB_alone_is_insufficient':True,
                    'pe_base':0x90000000,'constant_blob_sha256':sha(weights)},
          'pe_schedule':{'tile_MNK':[4,16,16],'operand_planes':2,'plane_radix':128,
                         'cache_K_blocks':8,'SRAM_addresses':16,'weight_reuse':'N tile -> 8 K blocks cached in 16 SRAM slots -> all M tiles',
                         'partial_reduction':'signed INT64 software sum; final checked INT32',
                         'dynamic_attention_B':'load runtime K/V/softmax-derived quantized tensor; never reuse stale constant weights',
                         'matrix_tiles':sum(n['matrix_tiles'] for n in macs),
                         'PE_passes_max':sum(n['pe_passes_max'] for n in macs)},
          'scope':'full-model static mapping and schedule; execution requires the RISC-V dispatcher and PE driver'}
    (destination/'plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2)+'\n')
    return plan
