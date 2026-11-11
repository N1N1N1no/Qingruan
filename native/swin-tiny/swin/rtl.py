"""Actual RV32 + full PE-array RTL checks using official-model intermediate data."""
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import numpy as np
import onnx
from .model import Model,sha
from .reference import session,preprocess

def run(competition,case):
    root=Path(__file__).resolve().parents[1];base=root.parents[1]
    task=base/'native/audits/20261107/riscv_pe_contest/contest/task1'
    project=task/'my_design/project';model=Model(competition);jobs=model.mapping()
    selected=[jobs[0],next(j for j in jobs if not j['B']['static']),
              next(j for j in jobs if j['op']=='MatMul' and j['B']['static'] and j['K']>=145)]
    names=list(dict.fromkeys(j[p]['tensor'] for j in selected for p in ['A','B'] if not j[p]['static']))
    graph=copy.deepcopy(model.model);del graph.graph.output[:]
    for name in names:graph.graph.output.append(onnx.helper.make_tensor_value_info(name,model.dtypes[name],model.shapes[name]))
    onnx.save(graph,case/'extract.onnx')
    image='ILSVRC2012_val_00000274.JPEG';values=session(case/'extract.onnx').run(names,{'input':preprocess(competition,image)})
    raw=model.constants.copy();raw.update(zip(names,values));matrices=[]
    def centered(q):
        value=raw[q['tensor']].astype(np.int64);zero=model.constants[q['zero_point']].astype(np.int64)
        if zero.ndim:
            shape=[1]*value.ndim;shape[q['axis']%value.ndim]=zero.size;zero=zero.reshape(shape)
        return value-zero
    for i,j in enumerate(selected):
        aa=centered(j['A']);bb=centered(j['B'])
        if i==0:
            a=np.array([[aa[0,c,y,x] for c in range(3) for y in range(4) for x in range(pos*4,pos*4+4)] for pos in range(4)],np.int16)[None]
            b=bb[:16].reshape(16,48).T.astype(np.int16)[None]
        elif i==1:
            a=aa.reshape(-1,j['M'],j['K'])[:2,:5,:].astype(np.int16)
            b=bb.reshape(-1,j['K'],j['N'])[:2,:,:1].astype(np.int16)
        else:
            a=aa.reshape(-1,j['K'])[:1,:145].astype(np.int16)[None]
            b=bb[:145,:1].astype(np.int16)[None]
        matrices.append((a,b,j))
    writes=[];reads=[];results=[];header=['#include <stdint.h>'];offset=0;vectors=[];metadata=[]
    def wr(addr,value):writes.append((0x90000000+addr,int(value)&0xffffffff))
    def planes(x,p):
        hi=(x.astype(np.int64)+64)//128
        return hi if p else x-128*hi
    def packed(x):return sum((int(abs(v))|(0x80 if v<0 else 0))<<(8*i) for i,v in enumerate(x))
    for vi,(a,b,job) in enumerate(matrices):
        batches,m,k=a.shape;n=b.shape[-1];answer=a.astype(np.int64)@b.astype(np.int64);results.extend(answer.ravel().tolist())
        for label,v in [('A',a),('B',b)]:header.append('static const int16_t %s_%d[]={%s};'%(label,vi,','.join(map(str,v.ravel()))))
        vectors.append('{A_%d,B_%d,%d,%d,%d,%d,%d}'%(vi,vi,batches,m,n,k,offset));offset+=answer.size
        metadata.append({'node':job['name'],'op':job['op'],'shape_BMNK':[batches,m,n,k],
                         'source_shape_A':job['input_shapes'][0],'source_shape_B':job['input_shapes'][1],
                         'scope':'selected positions/channels/terms of official-model tensors; not the entire layer',
                         'A_centered_range':[int(a.min()),int(a.max())],'B_centered_range':[int(b.min()),int(b.max())]})
        wr(8,65535)
        for index in range(64):wr(0x200+4*index,0)
        for batch in range(batches):
            for n0 in range(0,n,16):
                for kg in range(0,k,128):
                    blocks=min(8,(k-kg+15)//16)
                    for block in range(blocks):
                        for bp in range(2):
                            wr(0,2*block+bp)
                            for row in range(16):
                                kk=kg+block*16+row
                                weights=np.zeros(16,np.int64)
                                if kk<k:weights[:min(16,n-n0)]=planes(b[batch,kk,n0:n0+16],bp)
                                for word in range(4):wr(0x20+4*word,packed(weights[word*4:word*4+4]))
                                wr(0x10,1<<row)
                    for m0 in range(0,m,4):
                        for block in range(blocks):
                            for ap in range(2):
                                av=np.zeros((4,16),np.int64)
                                for row in range(16):
                                    kk=kg+block*16+row
                                    if kk<k:av[:min(4,m-m0),row]=planes(a[batch,m0:m0+4,kk],ap)
                                    wr(0x100+4*row,packed(av[:,row]))
                                for bp in range(2):
                                    wr(0,2*block+bp);bv=np.zeros((16,16),np.int64)
                                    end=min(k,kg+16*block+16);start=kg+16*block
                                    bv[:end-start,:min(16,n-n0)]=planes(b[batch,start:end,n0:n0+16],bp)
                                    partial=av@bv
                                    for index,v in enumerate(partial.T.ravel()):reads.append((0x90000400+4*index,int(v)&0xffffffff))
    header += ['typedef struct {const int16_t *a,*b;uint32_t batch,m,n,k,offset;} Vector;',
               'static const Vector VECTORS[]={'+','.join(vectors)+'};',f'#define VECTOR_COUNT {len(vectors)}',f'#define RESULT_COUNT {len(results)}']
    (case/'rtl_vectors.h').write_text('\n'.join(header)+'\n')
    for name,data in [('writes',writes),('reads',reads)]:
        (case/(name+'.hex')).write_text(''.join('%08x %08x\n'%pair for pair in data))
    (case/'results.hex').write_text(''.join('%08x\n'%(v&0xffffffff) for v in results))
    summary={'status':'RUNNING','scope':'representative official Swin tensors through actual RISC-V and unchanged full SoC/PE RTL',
             'full_model_rtl':False,'sdf_annotated':False,'model_sha256':sha(model.path),'image':image,'vectors':metadata,
             'expected_MMIO_writes':len(writes),'expected_MMIO_reads':len(reads),'CPU_results':len(results),'commands':[]}
    def command(argv,label,timeout=1200):
        argv=list(map(str,argv));log=case/(label+'.log')
        print('SWIN_RTL_PHASE='+label,flush=True)
        with log.open('w') as f:r=subprocess.run(argv,cwd=case,stdout=f,stderr=subprocess.STDOUT,timeout=timeout)
        summary['commands'].append({'argv':argv,'returncode':r.returncode,'log':str(log)})
        (case/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
        if r.returncode:raise RuntimeError(label+' failed: '+str(log))
        return log.read_text(errors='replace')
    prefix='/home/zhz/rtl_tools/xpack-riscv-none-elf-gcc-14.2.0-3/bin/riscv-none-elf-'
    addresses={}
    for name,negative in [('good',False),('bad',True)]:
        argv=[prefix+'gcc','-march=rv32imc_zicsr','-mabi=ilp32','-O2','-ffreestanding','-fno-builtin','-msmall-data-limit=0',
              '-nostdlib','-nostartfiles','-Wl,--no-relax','-Wl,-T,'+str(project/'sw/link.ld'),'-I'+str(case),'-I'+str(root/'c')]
        if negative:argv+=['-DSWIN_INJECT_ERROR']
        command(argv+[project/'sw/start.S',root/'c/pe_driver.c',root/'c/rtl_main.c','-lgcc','-o',case/(name+'.elf')],name+'_gcc')
        command([prefix+'objcopy','-O','binary',case/(name+'.elf'),case/(name+'.bin')],name+'_objcopy')
        data=(case/(name+'.bin')).read_bytes();(case/(name+'.hex')).write_text('\n'.join('%02x'%v for v in data)+'\n')
        text=command([prefix+'nm','-S',case/(name+'.elf')],name+'_symbols')
        match=re.search(r'^([0-9a-f]+)\s+([0-9a-f]+)\s+[Bb]\s+swin_rtl_results$',text,re.M)
        if not match or int(match[2],16)!=4*len(results):raise RuntimeError('Bad result symbol')
        addresses[name]=match[1]
    # Diagnostic strings only; preserve the verified RAM logic and 256 KiB size.
    ram=(project/'tb/axi_ram_model.sv').read_text().replace('SOC_SMOKE_PASS: CPU checked all 64 PE output words','SWIN_DRIVER_CPU_RETURN_PASS').replace('SOC_SMOKE_FAIL','SWIN_DRIVER_CPU_RETURN_FAIL')
    (case/'axi_ram_model.sv').write_text(ram)
    command(['/usr/bin/gcc','-fPIC','-c',task/'my_design/compat/pthread_yield_compat.c','-o',case/'compat.o'],'compat')
    command([os.environ.get('TASK1_PYTHON','/home/zhz/miniforge3/bin/python3'),task/'scripts/vcs_vm.py','-full64','-sverilog','-timescale=1ns/1ps','-debug_access',
             '+define+VCS','+define+PE_REFERENCE_MACRO','+define+AXI_RX_TIMING_CHECK','+define+AXI_RAM_OUTPUT_DELAY_NS=1.0',
             '-assert','svaext','-f',project/'build/rtl.f',project/'tb/sram_reference_model.v',case/'axi_ram_model.sv',project/'tb/tb_soc.sv',
             root/'tests/rtl_monitor.sv','-top','tb_soc','-top','swin_driver_monitor','-Mdir='+str(case/'csrc'),'-o',case/'simv','-LDFLAGS',case/'compat.o'],'compile')
    simulator_sha=sha(case/'simv')
    for name in ['good','bad']:
        args=[case/'simv','+MEM_HEX='+str(case/(name+'.hex')),'+WRITE_FILE='+str(case/'writes.hex'),'+READ_FILE='+str(case/'reads.hex'),
              '+RESULT_FILE='+str(case/'results.hex'),'+RESULT_ADDR='+addresses[name],'+EXPECTED_WRITES='+str(len(writes)),
              '+EXPECTED_READS='+str(len(reads)),'+RESULT_COUNT='+str(len(results)),'+TRACE='+str(case/(name+'_trace.tsv')),'+MAX_CYCLES=15000000']
        if name=='bad':args+=['+NEGATIVE']
        text=command(args,name+'_simulation')
        if name=='good':
            if text.count('SWIN_DRIVER_MONITOR_PASS')!=1 or text.count('SWIN_DRIVER_CPU_RETURN_PASS')!=1 or re.search(r'Fatal:|Error-\[|AXI_RX_(?:SETUP_FAIL|UNKNOWN)',text):raise RuntimeError('RTL normal rejected')
            summary['normal_monitor']=re.search(r'SWIN_DRIVER_MONITOR_PASS[^\n]+',text)[0]
        elif 'SWIN_MONITOR_FAIL arithmetic' not in text or 'SWIN_DRIVER_MONITOR_PASS' in text:raise RuntimeError('Negative test failed to reject arithmetic')
        assert sha(case/'simv')==simulator_sha
    summary.update(status='PASS',negative_control_detected=True,simulator_sha256=simulator_sha,
                   source_sha256={str(p):sha(p) for p in [root/'c/pe_driver.c',root/'c/rtl_main.c',root/'tests/rtl_monitor.sv',project/'tb/axi_ram_model.sv']})
    return summary
