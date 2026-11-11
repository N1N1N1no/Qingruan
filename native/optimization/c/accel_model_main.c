/* Bare-metal full graph: all dispatch/packing/remaining FP kernels run on RV32.
 * Descriptors, model constants, input and immutable tables are preloaded in
 * the explicitly configured 64 MiB external RAM. */
#include "graph.h"
#include "accel_io.h"
#include "model_layout.h"
#include <math.h>
#include <string.h>
typedef struct{uint32_t start,end,input,output,channels,elements,offset;}Fusion;
uint8_t *rtl_memory=(uint8_t *)(uintptr_t)0x80000000u;
volatile uint64_t inference_cycles;
volatile uint64_t node_cycles[MODEL_NODES];
volatile uint32_t model_error_node,model_mac_jobs,model_gelu_fusions,model_mac_commands;
volatile uint32_t model_quant_commands;
volatile uint32_t model_top1,model_expected_top1,model_max_error_bits;
int rtl_call(unsigned opcode,const uint32_t args[16],uint32_t *cycles,uint32_t *bytes){
 AccelResult result={0};int status=accel_execute(opcode,args,1000000000ull,&result);
 if(opcode==3)++model_mac_commands;
 *cycles=result.engine_cycles;*bytes=result.streamed_bytes;return status;
}
int main(void){
 SwinGraph g;const SwinHeader *h=(const SwinHeader *)(uintptr_t)MODEL_DESCRIPTOR_BASE;
 if(h->nodes!=MODEL_NODES||h->jobs!=77||h->reserved[0]!=1)return 0x90;
 int status=swin_graph_init(&g,h,(void *)(uintptr_t)h->constant_base,(void *)(uintptr_t)h->arena_base,0);
 if(status)return 0x91;
 const Fusion *fusions=(const Fusion *)(uintptr_t)MODEL_FUSION_BASE;uint32_t fi=0;
 __asm__ volatile("fence iorw,iorw":::"memory");uint64_t begin=accel_cycle64();
 for(uint32_t i=0;i<h->nodes;++i){
  uint32_t original=i;uint64_t node_begin=accel_cycle64();
  if(fi<MODEL_FUSIONS&&fusions[fi].start==i){
   const Fusion *f=fusions+fi++;
   if(f->end>=h->nodes||f->input>=h->tensors||f->output>=h->tensors||!f->channels||f->elements%f->channels)return 0x92;
   uint32_t args[16]={h->arena_base+g.t[f->input].offset,h->arena_base+g.t[f->output].offset,MODEL_GELU_BASE+f->offset,f->channels,f->elements/f->channels};
   uint32_t cycles,bytes;status=rtl_call(2,args,&cycles,&bytes);
   if(!status&&bytes!=f->elements)status=-96;
   g.executed_nodes+=f->end-f->start+1;i=f->end;
  }else if(g.n[i].executor==1&&g.n[i].op==16){
   const SwinNode *node=g.n+i;
   if(quant_offset_by_node[i]==0xffffffffu)return 0x97;
   AccelResult result={0};status=accel_quant((uint32_t)(uintptr_t)swin_tensor_data(&g,node->in[0]),(uint32_t)(uintptr_t)swin_tensor_data(&g,node->output),MODEL_QUANT_BASE+quant_offset_by_node[i],g.t[node->output].elements,1000000000ull,&result);
   if(!status&&result.streamed_bytes!=g.t[node->output].elements)status=-97;
   ++model_quant_commands;++g.executed_nodes;
  }else{status=swin_graph_node(&g,i);++g.executed_nodes;}
  node_cycles[original]=accel_cycle64()-node_begin;
  if(status){model_error_node=original+1;return 0x93;}
 }
 __asm__ volatile("fence iorw,iorw":::"memory");inference_cycles=accel_cycle64()-begin;
 model_mac_jobs=g.executed_jobs;model_gelu_fusions=fi;
 if(g.executed_jobs!=77||fi!=MODEL_FUSIONS||g.executed_nodes!=MODEL_NODES||model_quant_commands!=166)return 0x94;
 // Validation follows the timed region. Compare full logits to the frozen C
 // output and require identical Top-1; RV32/newlib FP is separately checked.
 const float *out=swin_tensor_data(&g,h->output),*expected=(const float *)(uintptr_t)MODEL_EXPECTED_BASE;
 uint32_t count=g.t[h->output].elements,actual_top=0,expected_top=0;float peak=0,max_error=0;
 for(uint32_t i=0;i<count;++i){
  if(!isfinite(out[i])||!isfinite(expected[i]))return 0x95;
  float error=fabsf(out[i]-expected[i]),magnitude=fabsf(expected[i]);
  if(error>max_error)max_error=error;if(magnitude>peak)peak=magnitude;
  if(out[i]>out[actual_top])actual_top=i;if(expected[i]>expected[expected_top])expected_top=i;
 }
 model_top1=actual_top;model_expected_top1=expected_top;memcpy((void *)&model_max_error_bits,&max_error,4);
 if(actual_top!=expected_top||max_error>2e-6f*fmaxf(1.0f,peak))return 0x96;
 __asm__ volatile("fence iorw,iorw":::"memory");return 0;
}
