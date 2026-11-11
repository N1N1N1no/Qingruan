"""Measure the actual C non-MAC dispatcher on the unchanged RV32 SoC RTL."""
import json,math,os,re,struct,subprocess
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parent;BASE=ROOT.parents[1]
SWIN=BASE/'native/swin-tiny';TASK=BASE/'native/audits/20261107/riscv_pe_contest/contest/task1';PROJECT=TASK/'my_design/project'
source=SWIN/'work/verify.ylnpqxth';plan=json.loads((source/'plan.json').read_text())
case=ROOT/'work/cpu-profile';case.mkdir(parents=True,exist_ok=True)
original_constants=(source/'constants.bin').read_bytes()
def get(t):
    if t['storage']=='constant':return np.frombuffer(original_constants[t['offset']:t['offset']+t['bytes']],dtype=t['dtype']).copy().reshape(t['shape'])
    return np.fromfile(source/'teacher'/('tensor_%04d.bin'%t['id']),dtype=t['dtype']).reshape(t['shape'])
ops=['QuantizeLinear','DequantizeLinear','Add','Mul','Div','Erf','LayerNormalization','Softmax','Transpose','Reshape']
opcodes=['Constant','Identity','Reshape','Flatten','Shape','Transpose','Slice','Gather','Concat','Add','Mul','Div','Erf','LayerNormalization','Softmax','GlobalAveragePool','QuantizeLinear','DequantizeLinear','Conv','MatMul','Gemm']
dtype={'float32':1,'int8':3,'uint8':2,'int32':6,'int64':7}
headers=['#include <stdint.h>'];probes=[];metadata=[];totalwords=0;maxarena=0
for index,op in enumerate(ops):
    ns=[n for n in plan['schedule'] if n['op']==op and n['executor']=='riscv']
    if op=='DequantizeLinear':ns=[n for n in ns if plan['tensors'][n['inputs'][0]]['dtype']=='int8']
    n=ns[0];ts=[plan['tensors'][i] for i in n['inputs']];out=plan['tensors'][n['outputs'][0]]
    arrays=[get(t) for t in ts]
    if op=='LayerNormalization':shape=[1,1,96];oshape=shape
    elif op=='Softmax':shape=[1,1,1,49];oshape=shape
    elif op=='Add':shape0=[288];shape=[1,1,288];oshape=shape
    elif op=='Transpose':shape=[1,16,32];oshape=[1,32,16]
    elif op=='Reshape':shape=[1,16,32];oshape=[1,32,16]
    else:shape=[128];oshape=shape
    selected=[]
    for slot,a in enumerate(arrays):
        sh=shape0 if op=='Add' and slot==0 else shape if slot==0 or op=='Add' and slot==1 else list(a.shape)
        if op=='LayerNormalization' and slot>0:sh=[96]
        selected.append(a.ravel()[:math.prod(sh)].reshape(sh))
    blob=bytearray();tensors=bytearray()
    for a in selected:
        offset=(len(blob)+63)//64*64;blob+=b'\0'*(offset-len(blob));blob+=a.tobytes()
        dims=list(a.shape);tensors+=struct.pack('<6I8i',offset,a.size,dtype[str(a.dtype)],len(dims),0,0,*(dims+[1]*(8-len(dims))))
    elements=math.prod(oshape);bytesout=elements*np.dtype(out['dtype']).itemsize
    tensors+=struct.pack('<6I8i',0,elements,dtype[out['dtype']],len(oshape),1,0,*(oshape+[1]*(8-len(oshape))))
    attr=[0]*16;attr[0]=n['attributes'].get('axis',1 if op in ['QuantizeLinear','DequantizeLinear'] else -1)
    if op=='Transpose':attr[:3]=[0,2,1]
    ins=list(range(len(selected)));node=struct.pack('<4I8i16i4fi',opcodes.index(op),1,len(ins),len(ins),*(ins+[-1]*(8-len(ins))),*attr,n['attributes'].get('epsilon',1e-5),0,0,0,-1)
    head=struct.pack('<16I',0x4e495753,1,len(ins)+1,1,0,0,len(ins),64,64+len(tensors),64+len(tensors)+len(node),len(blob),bytesout,0,0,0,0)
    graph=head+tensors+node
    for label,data in [('G',graph),('C',blob)]:headers.append('static const uint8_t %s%d[] __attribute__((aligned(8)))={%s};'%(label,index,','.join(map(str,data))))
    probes.append('{G%d,C%d,%d,%d}'%(index,index,elements,bytesout));metadata.append({'op':op,'node':n['node_index'],'elements':elements,'bytes':bytesout,'dtype':out['dtype'],'word_offset':totalwords})
    totalwords+=(bytesout+3)//4;maxarena=max(maxarena,bytesout)
headers+=['typedef struct{const void *graph,*constants;uint32_t elements,bytes;}Probe;','static const Probe PROBES[]={'+','.join(probes)+'};',f'#define PROFILE_COUNT {len(probes)}',f'#define PROFILE_WORDS {totalwords}',f'#define PROFILE_ARENA {maxarena}']
(case/'profile_vectors.h').write_text('\n'.join(headers)+'\n');(case/'probes.json').write_text(json.dumps(metadata,indent=2)+'\n')
commands=[]
def run(args,name):
    print('CPU_PROFILE_PHASE='+name,flush=True);args=list(map(str,args))
    with (case/(name+'.log')).open('w') as log:r=subprocess.run(args,cwd=case,stdout=log,stderr=subprocess.STDOUT,timeout=1200)
    commands.append({'argv':args,'returncode':r.returncode});
    if r.returncode:raise RuntimeError(name+' failed')
    return (case/(name+'.log')).read_text(errors='replace')
sources=[SWIN/'c/graph.c',SWIN/'c/pe_driver.c',ROOT/'c/profile_cpu.c']
run(['/usr/bin/gcc','-O2','-ffp-contract=off','-fno-builtin','-DSWIN_HOST','-DSWIN_PROFILE_HOST','-I'+str(SWIN/'c'),'-I'+str(case),*sources,'-lm','-o',case/'host_run'],'host_compile')
run([case/'host_run',case/'host-values.bin'],'host_execute')
prefix='/home/zhz/rtl_tools/xpack-riscv-none-elf-gcc-14.2.0-3/bin/riscv-none-elf-'
run([prefix+'gcc','-march=rv32imc_zicsr','-mabi=ilp32','-O2','-ffp-contract=off','-ffreestanding','-fno-builtin','-msmall-data-limit=0','-nostartfiles','-Wl,--no-relax','-Wl,-T,'+str(PROJECT/'sw/link.ld'),
     '-I'+str(SWIN/'c'),'-I'+str(case),PROJECT/'sw/start.S',*sources,'-lm','-lc','-lgcc','-o',case/'program.elf'],'rv_compile')
run([prefix+'objcopy','-O','binary',case/'program.elf',case/'program.bin'],'objcopy')
(case/'program.hex').write_text('\n'.join('%02x'%v for v in (case/'program.bin').read_bytes())+'\n')
syms=run([prefix+'nm','-S',case/'program.elf'],'symbols')
addresses={name:re.search(r'^([0-9a-f]+)\s+[0-9a-f]+\s+[Bb]\s+'+name+'$',syms,re.M)[1] for name in ['profile_cycles','profile_values']}
ram=(PROJECT/'tb/axi_ram_model.sv').read_text().replace('SOC_SMOKE_PASS: CPU checked all 64 PE output words','PROFILE_CPU_RETURN_PASS').replace('SOC_SMOKE_FAIL','PROFILE_CPU_RETURN_FAIL')
(case/'axi_ram_model.sv').write_text(ram)
run(['/usr/bin/gcc','-fPIC','-c',TASK/'my_design/compat/pthread_yield_compat.c','-o',case/'compat.o'],'compat')
run(['/home/zhz/miniforge3/bin/python3',TASK/'scripts/vcs_vm.py','-full64','-sverilog','-timescale=1ns/1ps','-debug_access','+define+VCS','+define+PE_REFERENCE_MACRO','+define+AXI_RX_TIMING_CHECK','+define+AXI_RAM_OUTPUT_DELAY_NS=1.0','-assert','svaext',
     '-f',PROJECT/'build/rtl.f',PROJECT/'tb/sram_reference_model.v',case/'axi_ram_model.sv',PROJECT/'tb/tb_soc.sv',ROOT/'tests/profile_monitor.sv','-top','tb_soc','-top','cpu_profile_monitor','-Mdir='+str(case/'csrc'),'-o',case/'simv','-LDFLAGS',case/'compat.o'],'compile')
text=run([case/'simv','+MEM_HEX='+str(case/'program.hex'),'+CYCLE_ADDR='+addresses['profile_cycles'],'+DATA_ADDR='+addresses['profile_values'],
          '+COUNT='+str(len(probes)),'+WORDS='+str(totalwords),'+CYCLE_FILE='+str(case/'cycles.hex'),'+DATA_FILE='+str(case/'values.hex'),'+MAX_CYCLES=25000000'],'simulate')
if 'CPU_PROFILE_MONITOR_PASS' not in text or 'PROFILE_CPU_RETURN_PASS' not in text or re.search(r'Fatal:|Error-\[|AXI_RX_(?:SETUP_FAIL|UNKNOWN)',text):raise RuntimeError('CPU profile simulation rejected')
cycledata=[int(v,16) for v in (case/'cycles.hex').read_text().split()];data=b''.join(struct.pack('<I',int(v,16)) for v in (case/'values.hex').read_text().split());host=(case/'host-values.bin').read_bytes()
for i,m in enumerate(metadata):
    cycles,elements,status,size=cycledata[4*i:4*i+4];assert cycles>0 and not status and elements==m['elements'] and size==m['bytes']
    start=m['word_offset']*4;a=np.frombuffer(data[start:start+size],dtype=m['dtype']);b=np.frombuffer(host[start:start+size],dtype=m['dtype']);diff=np.abs(a.astype(np.float64)-b.astype(np.float64))
    m.update(cycles=cycles,cycles_per_element=cycles/elements,max_abs_vs_host=float(diff.max()),peak_normalized_error=float(diff.max()/max(float(np.max(np.abs(b))),1e-12)),bit_exact_vs_host=bool(np.array_equal(a,b)))
summary={'status':'PASS','scope':'actual RV32 C non-MAC dispatcher microbenchmarks on unmodified full SoC RTL; not full-model timing','probes':metadata,'commands':commands,'full_model_measured':False}
(case/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print('CPU_PROFILE_PASS',flush=True)
