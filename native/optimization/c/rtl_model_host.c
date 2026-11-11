/* Functional full-graph bridge. Host runs the unchanged non-fused CPU kernels;
 * the RTL backend performs every MAC and each fused GELU through its AXI RAM.
 * Host execution time and packing are excluded: no full-SoC latency claim. */
#include "graph.h"
#include "svdpi.h"
#include "quant_layout.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
extern int rtl_command(unsigned,const svBitVecVal *,unsigned *,unsigned *);
typedef struct{uint32_t start,end,input,output,channels,elements,offset;}Fusion;
uint8_t *rtl_memory;
static uint32_t calls[5];static uint64_t cycles_by_opcode[5],bytes_by_opcode[5];
static int memory_error;
#include "rtl_model_checkpoint.h"
static void *read_file(const char *p,size_t *size){
 FILE *f=fopen(p,"rb");if(!f){perror(p);return 0;}
 if(fseek(f,0,SEEK_END)){fclose(f);return 0;}long n=ftell(f);rewind(f);
 if(n<0){fclose(f);return 0;}void *data=malloc(n?n:1);
 if(!data||fread(data,1,n,f)!=(size_t)n){fclose(f);free(data);return 0;}
 fclose(f);*size=(size_t)n;return data;
}
unsigned long long c_read64(unsigned address){
 if(address<0x80000000u||(uint64_t)address+8>0x84000000ull||address&7){memory_error=1;return 0;}
 uint64_t value;memcpy(&value,rtl_memory+(address-0x80000000u),8);return value;
}
void c_write64(unsigned address,unsigned long long data,unsigned char strobes){
 if(address<0x80000000u||(uint64_t)address+8>0x84000000ull||address&7){memory_error=1;return;}
 for(unsigned i=0;i<8;++i)if(strobes&(1u<<i))rtl_memory[address-0x80000000u+i]=(uint8_t)(data>>(8*i));
}
int rtl_call(unsigned opcode,const uint32_t args[16],uint32_t *cycles,uint32_t *bytes){
 unsigned error=0,count=0,nbytes=0;
 extern void rtl_last_bytes(unsigned *);
 if(opcode<1||opcode>4||rtl_command(opcode,(const svBitVecVal *)args,&count,&error))return -1;
 rtl_last_bytes(&nbytes);
 if(error||memory_error){fprintf(stderr,"RTL_CALL_FAIL op=%u error=%u memory=%d\n",opcode,error,memory_error);return -2;}
 ++calls[opcode];cycles_by_opcode[opcode]+=count;bytes_by_opcode[opcode]+=nbytes;
 *cycles=count;*bytes=nbytes;return 0;
}
int c_run_model(const char *plan_dir,const char *input_path,const char *expected_path,const char *output_path,const char *report_path,
 const char *checkpoint_path,const char *identity,unsigned long long checkpoint_interval,int stop_after_checkpoint,int *status){
 *status=1;memory_error=0;memset(calls,0,sizeof(calls));memset(cycles_by_opcode,0,sizeof(cycles_by_opcode));memset(bytes_by_opcode,0,sizeof(bytes_by_opcode));
 rtl_memory=calloc(0x04000000u,1);if(!rtl_memory)return 0;
 char path[4096];size_t size;
 snprintf(path,sizeof(path),"%s/graph.bin",plan_dir);void *image=read_file(path,&size);if(!image)return 0;
 SwinHeader *h=image;if(size<sizeof(*h)||h->magic!=0x4e495753||h->reserved[0]!=1)return 0;
 if(h->constant_base<0x80000000u||(uint64_t)h->constant_base+h->constant_bytes>0x83000000ull||h->arena_base<0x80000000u||(uint64_t)h->arena_base+h->arena_bytes>0x83000000ull)return 0;
 snprintf(path,sizeof(path),"%s/constants.bin",plan_dir);void *constants=read_file(path,&size);if(!constants||size!=h->constant_bytes)return 0;
 memcpy(rtl_memory+h->constant_base-0x80000000u,constants,size);free(constants);
 SwinGraph g;if(swin_graph_init(&g,image,rtl_memory+h->constant_base-0x80000000u,rtl_memory+h->arena_base-0x80000000u,0))return 0;
 void *input=read_file(input_path,&size);if(!input||size!=(size_t)g.t[h->input].elements*4)return 0;
 memcpy(swin_tensor_data(&g,h->input),input,size);free(input);
 snprintf(path,sizeof(path),"%s/gelu-fusions.bin",plan_dir);Fusion *fusions=read_file(path,&size);if(!fusions||size%sizeof(Fusion))return 0;
 size_t nf=size/sizeof(Fusion),fi=0;
 // The caller stages the immutable table beside the descriptor.
 snprintf(path,sizeof(path),"%s/gelu.bin",plan_dir);void *tables=read_file(path,&size);
 uint32_t table_base=h->arena_base+h->arena_bytes;
 if(!tables||table_base&63||(uint64_t)table_base+size>0x83000000ull)return 0;
 uint32_t quant_base=table_base+(uint32_t)size;
 memcpy(rtl_memory+table_base-0x80000000u,tables,size);free(tables);
 snprintf(path,sizeof(path),"%s/quant-thresholds.bin",plan_dir);tables=read_file(path,&size);
 if(!tables||(uint64_t)quant_base+size>0x83000000ull)return 0;
 memcpy(rtl_memory+quant_base-0x80000000u,tables,size);free(tables);
 uint32_t next_node=0,segments=1;
 if(strlen(identity)!=64)return 0;
 int resumed=load_checkpoint(checkpoint_path,identity,&g,nf,&next_node,&fi,&segments);if(resumed<0){fprintf(stderr,"RTL_MODEL_BAD_CHECKPOINT\n");return 0;}
 uint64_t last_checkpoint=engine_total();
 for(uint32_t i=next_node;i<h->nodes;++i){
  int rc;
  if(fi<nf&&fusions[fi].start==i){
   const Fusion *f=fusions+fi++;
   if(f->end>=h->nodes||f->input>=h->tensors||f->output>=h->tensors||!f->channels||f->elements%f->channels)return 0;
   uint32_t args[16]={h->arena_base+g.t[f->input].offset,h->arena_base+g.t[f->output].offset,table_base+f->offset,f->channels,f->elements/f->channels};
   uint32_t clocks,bytes;rc=rtl_call(2,args,&clocks,&bytes);g.executed_nodes+=f->end-f->start+1;i=f->end;
   if(!rc&&bytes!=f->elements)rc=-96;
  }else if(g.n[i].executor==1&&g.n[i].op==16){
   const SwinNode *node=g.n+i;
   if(quant_offset_by_node[i]==0xffffffffu)return 0;
   uint32_t args[16]={h->arena_base+g.t[node->in[0]].offset,h->arena_base+g.t[node->output].offset,quant_base+quant_offset_by_node[i],g.t[node->output].elements};
   uint32_t clocks,bytes;rc=rtl_call(4,args,&clocks,&bytes);++g.executed_nodes;
   if(!rc&&bytes!=g.t[node->output].elements)rc=-97;
  }else{rc=swin_graph_node(&g,i);++g.executed_nodes;}
  if(rc){fprintf(stderr,"RTL_MODEL_NODE_FAIL node=%u code=%d\n",i,rc);return 0;}
  if(g.n[i].executor==3||fi&&fusions[fi-1].end==i){printf("RTL_MODEL_PROGRESS node=%u MAC_jobs=%u GELU=%zu\n",i,g.executed_jobs,fi);fflush(stdout);}
  if(checkpoint_interval&&(engine_total()-last_checkpoint>=checkpoint_interval||i+1==h->nodes)){
   if(save_checkpoint(checkpoint_path,identity,&g,i+1,fi,segments))return 0;
   last_checkpoint=engine_total();if(stop_after_checkpoint){*status=2;return 0;}
  }
 }
 if(g.executed_jobs!=h->jobs||fi!=nf||calls[2]!=12||calls[4]!=166||memory_error)return 0;
 void *expected=read_file(expected_path,&size);size_t out_bytes=(size_t)g.t[h->output].elements*4;
 void *output=swin_tensor_data(&g,h->output);
 FILE *out=fopen(output_path,"wb");if(!out)return 0;fwrite(output,1,out_bytes,out);fclose(out);
 if(!expected||size!=out_bytes||memcmp(expected,output,size)){fprintf(stderr,"RTL_MODEL_LOGITS_MISMATCH\n");return 0;}
 FILE *report=fopen(report_path,"w");if(!report)return 0;
 fprintf(report,"{\"status\":\"PASS\",\"MAC_jobs\":%u,\"GELU_fusions\":%zu,\"quantizers\":%u,\"MAC_commands\":%u,\"MAC_engine_cycles\":%llu,\"GELU_engine_cycles\":%llu,\"quantizer_engine_cycles\":%llu,\"MAC_write_bytes\":%llu,\"GELU_write_bytes\":%llu,\"bit_exact_logits\":true,\"full_RV32_SoC\":false,\"host_CPU_and_packing_time_excluded\":true}\n",g.executed_jobs,fi,calls[4],calls[3],(unsigned long long)cycles_by_opcode[3],(unsigned long long)cycles_by_opcode[2],(unsigned long long)cycles_by_opcode[4],(unsigned long long)bytes_by_opcode[3],(unsigned long long)bytes_by_opcode[2]);fclose(report);
 printf("RTL_FULL_GRAPH_FUNCTIONAL_PASS MAC_jobs=%u GELU=%zu quantizers=%u commands=%u\n",g.executed_jobs,fi,calls[4],calls[3]);fflush(stdout);
 *status=0;free(expected);free(fusions);free(image);free(rtl_memory);rtl_memory=0;return 0;
}
