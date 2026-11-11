"""Locate the first graph difference without changing the frozen graph source."""
import hashlib,json,os,re,struct,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parent;BASE=ROOT.parents[1];SWIN=BASE/'native/swin-tiny'
case=ROOT/'work/newlib-difference';case.mkdir(parents=True,exist_ok=True)
original=ROOT/'c/host_opt.c';text=original.read_text()
helper=r'''
static void trace_node(SwinGraph *g,uint32_t node){
 const SwinTensor *t=g->t+g->n[node].output;const uint8_t *p=swin_tensor_data(g,g->n[node].output);
 if(!p){printf("NODE %u %u %u %u unallocated\n",node,g->n[node].op,t->dtype,t->elements);return;}
 size_t bytes=(size_t)t->elements*width(t->dtype);uint64_t hash=UINT64_C(14695981039346656037);
 for(size_t i=0;i<bytes;++i){hash^=p[i];hash*=UINT64_C(1099511628211);}
 printf("NODE %u %u %u %u %016llx\n",node,g->n[node].op,t->dtype,t->elements,(unsigned long long)hash);
 const char *dir=getenv("DUMP_DIR");const char *a=getenv("DUMP_A"),*b=getenv("DUMP_B");
 if(dir&&((a&&node==(uint32_t)strtoul(a,0,10))||(b&&node==(uint32_t)strtoul(b,0,10)))){
  char path[4096];snprintf(path,sizeof(path),"%s/node_%u.bin",dir,node);FILE *f=fopen(path,"wb");if(!f)exit(20);fwrite(p,1,bytes,f);fclose(f);
  if(g->n[node].inputs&&g->n[node].in[0]>=0){
   const SwinTensor *in=g->t+g->n[node].in[0];const void *data=swin_tensor_data(g,g->n[node].in[0]);
   if(data){snprintf(path,sizeof(path),"%s/node_%u_input.bin",dir,node);f=fopen(path,"wb");if(!f)exit(21);fwrite(data,width(in->dtype),in->elements,f);fclose(f);}
  }
 }
}
'''
text=text.replace('static int fuse(',helper+'\nstatic int fuse(')
needle='  if(status){fprintf(stderr,"node=%u error=%d\\n",i,status);return 6;}'
assert text.count(needle)==1;text=text.replace(needle,needle+'\n  trace_node(&g,i);')
source=case/'traced_host.c';source.write_text(text)
wrapper=case/'wrap.c';wrapper.write_text('''#include <stdlib.h>
extern float reference_newlib_expf(float),__real_expf(float);
float __wrap_expf(float x){return getenv("USE_NEWLIB_EXP")?reference_newlib_expf(x):__real_expf(x);}
''')
files=[SWIN/'c/graph.c',SWIN/'c/pe_driver.c',source,ROOT/'work/exp-reference/reference.c',wrapper]
subprocess.run(['/usr/bin/gcc','-O3','-march=native','-fno-builtin-expf','-ffp-contract=off','-DSWIN_HOST','-I'+str(SWIN/'c'),*map(str,files),'-Wl,--wrap=expf','-lm','-o',str(case/'host')],check=True)
image='ILSVRC2012_val_00000759.JPEG';plan=ROOT/'work/fused-plan';baseline=ROOT/'work/fusion-proof';tables=ROOT/'work/lookup-tables'
def run(kind,dump=None):
 directory=case/kind;directory.mkdir(exist_ok=True)
 env=os.environ.copy();env.pop('USE_NEWLIB_EXP',None)
 if kind=='newlib':env['USE_NEWLIB_EXP']='1'
 if dump:env.update(DUMP_DIR=str(directory),DUMP_A=str(dump[0]),DUMP_B=str(dump[1]))
 argv=[case/'host',plan/'graph.bin',plan/'constants.bin',baseline/(image+'.input'),directory/'logits.bin',tables/'gelu.bin',plan/'gelu-fusions.bin']
 with(directory/'trace.log').open('w')as log:subprocess.run(list(map(str,argv)),env=env,stdout=log,stderr=subprocess.STDOUT,timeout=180,check=True)
 return [tuple(row.split()[1:])for row in(directory/'trace.log').read_text().splitlines()if row.startswith('NODE ')]
print('NEWLIB_TRACE_PHASE=compare',flush=True);a=run('glibc');b=run('newlib');assert len(a)==len(b)
assert(case/'glibc/logits.bin').read_bytes()==(baseline/(image+'.baseline')).read_bytes()
differences=[]
for x,y in zip(a,b):
 assert x[:4]==y[:4]
 if x[4]!=y[4]:differences.append({'node':int(x[0]),'op':int(x[1]),'dtype':int(x[2]),'elements':int(x[3])})
assert differences
first=differences[0];first_quant=next(x for x in differences if x['op']==16)
associated_softmax=next(x for x in reversed(differences)if x['node']<first_quant['node']and x['op']==14)
print('NEWLIB_TRACE_PHASE=dump first='+str(first['node'])+' quant='+str(first_quant['node']),flush=True)
run('glibc',(first['node'],first_quant['node']));run('newlib',(first['node'],first_quant['node']))
run('glibc',(associated_softmax['node'],first_quant['node']));run('newlib',(associated_softmax['node'],first_quant['node']))
def pair(node):return [(case/kind/('node_%d.bin'%node)).read_bytes()for kind in['glibc','newlib']]
ra,rb=pair(first['node']);assert first['dtype']==1
fa=struct.unpack('<%df'%(len(ra)//4),ra);fb=struct.unpack('<%df'%(len(rb)//4),rb)
ua=struct.unpack('<%dI'%len(fa),ra);ub=struct.unpack('<%dI'%len(fb),rb)
qa,qb=pair(first_quant['node']);changed=[i for i,(x,y)in enumerate(zip(qa,qb))if x!=y]
node=associated_softmax['node'];input_a=(case/'glibc'/('node_%d_input.bin'%node)).read_bytes();input_b=(case/'newlib'/('node_%d_input.bin'%node)).read_bytes()
assert input_a==input_b,'earlier differences reached the associated Softmax input'
assert len(input_a)==associated_softmax['elements']*4
raw_graph=(plan/'graph.bin').read_bytes();h=struct.unpack_from('<16I',raw_graph);node_fields=struct.unpack_from('<4I',raw_graph,h[8]+node*132)
tensor=struct.unpack_from('<6I8i',raw_graph,h[7]+node_fields[3]*56);channels=tensor[6+tensor[3]-1];assert channels==49
row_index=changed[0]//channels;row_offset=row_index*channels*4
(case/'threshold-row-input.bin').write_bytes(input_a[row_offset:row_offset+channels*4])
for kind in['glibc','newlib']:
 raw=(case/kind/('node_%d.bin'%node)).read_bytes();(case/('threshold-row-'+kind+'.bin')).write_bytes(raw[row_offset:row_offset+channels*4])
summary={'status':'DIFFERENCE_LOCALIZED_NOT_ACCURACY_PASS','image':image,'first_difference':first,'first_quantized_difference':first_quant,
 'first_float_changed_values':sum(x!=y for x,y in zip(ua,ub)),'first_float_max_abs_error':max(abs(x-y)for x,y in zip(fa,fb)),
 'first_float_max_unsigned_ULP_difference':max(abs(x-y)for x,y in zip(ua,ub)),
 'first_quantized_changed_values':len(changed),'first_quantized_changes':[{'index':i,'glibc_raw':qa[i],'newlib_raw':qb[i]}for i in changed[:16]],
 'Softmax_directly_before_first_quantized_difference':associated_softmax,'its_input_byte_identical':True,'captured_row':row_index,'captured_channels':channels,
 'different_node_outputs':len(differences),'source_sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest()for p in files+[original]},
 'scope':'one compiled graph/plan/inputs, only expf selected by wrapper, frozen C identity of glibc run checked; diagnosis does not authorize tolerance changes or model/preprocess/label tuning'}
(case/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps({k:v for k,v in summary.items()if k!='source_sha256'}),flush=True)
