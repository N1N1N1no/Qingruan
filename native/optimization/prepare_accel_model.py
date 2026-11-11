"""Build the real RV32 full-model artifacts; build success is not a run PASS."""
import argparse
import hashlib
import json
import re
import struct
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parent;BASE=ROOT.parents[1]
SWIN=BASE/'native/swin-tiny'
TASK=BASE/'native/audits/20261107/riscv_pe_contest/contest/task1';PROJECT=TASK/'my_design/project'
parser=argparse.ArgumentParser();parser.add_argument('--image',default='ILSVRC2012_val_00000274.JPEG');args=parser.parse_args()
assert re.fullmatch(r'ILSVRC2012_val_\d{8}\.JPEG',args.image)
case=ROOT/'work/accel-model';case.mkdir(parents=True,exist_ok=True)
source=ROOT/'work/fused-plan';plan=json.loads((source/'plan.json').read_text());memory=plan['memory']
graph=(source/'graph.bin').read_bytes();fusion_base=memory['descriptor_base']+(len(graph)+63)//64*64
fusions=(source/'gelu-fusions.bin').read_bytes();assert fusion_base+len(fusions)<=memory['descriptor_base']+memory['descriptor_capacity']
expected_base=0x83fff000
for n in plan['schedule']:
 if n['executor']=='pe':
  j=n['job'];scratch=j['M']*j['K']+j['K']*j['N']+j['M']*j['N']*4+128
  assert 0x83000000+scratch<=expected_base
layout={'NODES':len(plan['schedule']),'FUSIONS':len(fusions)//28,'DESCRIPTOR_BASE':memory['descriptor_base'],
 'FUSION_BASE':fusion_base,'GELU_BASE':memory['gelu_table_base'],'QUANT_BASE':memory['quant_table_base'],'EXPECTED_BASE':expected_base}
quant_offsets=[0xffffffff]*len(plan['schedule'])
for q in json.loads((ROOT/'work/lookup-tables/quant-thresholds.json').read_text()):quant_offsets[q['node']]=q['threshold_offset']
(case/'model_layout.h').write_text('\n'.join('#define MODEL_%s %su'%(k,hex(v)) for k,v in layout.items())+'\nstatic const uint32_t quant_offset_by_node[MODEL_NODES]={'+','.join(hex(v)+'u' for v in quant_offsets)+'};\n')
original=(SWIN/'c/graph.c').read_text();marker='static int mac(SwinGraph *g,const SwinNode *n) {'
old='''#ifdef SWIN_HOST
    if(!g->bus)status=host_mac(&c);else
#endif
    status=swin_pe_gemm(g->bus,&job,&g->stats);'''
assert original.count(marker)==original.count(old)==1
adapted=original.replace(marker,(ROOT/'c/rtl_mac_hook.inc').read_text()+'\n'+marker).replace(old,'    status=rtl_mac(&c);')
(case/'graph_accel.c').write_text(adapted)
prefix='/home/zhz/rtl_tools/xpack-riscv-none-elf-gcc-14.2.0-3/bin/riscv-none-elf-'
argv=[prefix+'gcc','-march=rv32imc_zicsr','-mabi=ilp32','-O2','-ffp-contract=off','-ffreestanding','-fno-builtin','-msmall-data-limit=0',
 '-nostartfiles','-Wl,--no-relax','-Wl,-T,'+str(PROJECT/'sw/link.ld'),'-Wl,-Map,'+str(case/'program.map'),
 '-I'+str(SWIN/'c'),'-I'+str(ROOT/'c'),'-I'+str(case),str(PROJECT/'sw/start.S'),str(case/'graph_accel.c'),
 str(ROOT/'c/accel_io.c'),str(ROOT/'c/accel_model_main.c'),'-lm','-lc','-lgcc','-o',str(case/'program.elf')]
with (case/'compile.log').open('w') as log:subprocess.run(argv,stdout=log,stderr=subprocess.STDOUT,check=True)
subprocess.run([prefix+'objcopy','-O','binary',str(case/'program.elf'),str(case/'program.bin')],check=True)
symbols=subprocess.check_output([prefix+'nm','-S',str(case/'program.elf')],text=True);(case/'symbols.txt').write_text(symbols)
firmware=(case/'program.bin').read_bytes();assert len(firmware)<memory['firmware_bytes']
(case/'program.hex').write_text(''.join('%02x\n'%v for v in firmware))
tensor=next(t for t in plan['tensors'] if t['name']=='input')
proof=ROOT/'work/fusion-proof'
sections=[(memory['firmware_base'],firmware),(memory['descriptor_base'],graph),(fusion_base,fusions),
 (memory['constant_base'],(source/'constants.bin').read_bytes()),
 (memory['activation_base']+tensor['offset'],(proof/(args.image+'.input')).read_bytes()),
 (memory['gelu_table_base'],(ROOT/'work/lookup-tables/gelu.bin').read_bytes()),
 (memory['quant_table_base'],(ROOT/'work/lookup-tables/quant-thresholds.bin').read_bytes()),
 (expected_base,(proof/(args.image+'.baseline')).read_bytes())]
sections.sort()
for (address,data),(next_address,_) in zip(sections,sections[1:]):assert address+len(data)<=next_address
with (case/'data.hex').open('w') as out:
 for address,data in sections:
  assert 0x80000000<=address and address+len(data)<=0x84000000
  out.write('@%08x\n'%(address-0x80000000));out.write(''.join('%02x\n'%v for v in data))
manifest={'status':'RV32_BUILD_AND_RAM_IMAGE_PASS_EXECUTION_PENDING','full_RV32_executed':False,'image':args.image,
 'external_RAM_bytes':67108864,'layout':layout,'firmware_bytes':len(firmware),'compiler_argv':argv,
 'RAM_sections':[{'address':hex(a),'bytes':len(d),'sha256':hashlib.sha256(d).hexdigest()} for a,d in sections],
 'inputs_sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [ROOT/'c/accel_io.c',ROOT/'c/accel_model_main.c',ROOT/'c/rtl_mac_hook.inc',SWIN/'c/graph.c']}}
(case/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n');print('ACCEL_MODEL_RV32_BUILD_PASS_EXECUTION_PENDING',flush=True)
