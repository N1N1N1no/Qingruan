/* Synthetic raw-tensor Conv and broadcast MatMul integration, no model run. */
#include "mac_plan.h"
#include "mac_dispatch.h"
#include <string.h>
static uint8_t memory[8192] __attribute__((aligned(64))),expected[4096];
static SwinHeader header;static SwinTensor tensors[8];static SwinNode node;static SwinJob job;static SwinGraph graph;
volatile uint32_t mac_plan_stage,mac_plan_failure,mac_plan_commands;
volatile uint64_t mac_plan_engine_cycles,mac_plan_streamed_bytes;
void *swin_tensor_data(SwinGraph *g,uint32_t id){const SwinTensor *t=g->t+id;return t->storage==0?(void *)(g->constants+t->offset):t->storage==1?g->arena+t->offset:0;}
static void tensor(unsigned id,unsigned offset,unsigned count,unsigned type,unsigned rank,int a,int b,int c,int d){tensors[id]=(SwinTensor){.offset=offset,.elements=count,.dtype=type,.rank=rank,.storage=id==1?0:1,.dims={a,b,c,d}};}
static int run(unsigned which){
 memset(memory,0xa5,sizeof(memory));memset(tensors,0,sizeof(tensors));memset(&node,0,sizeof(node));
 unsigned m=which?5:16,n=3,k=which?9:12,batches=which?2:1,trans=which==2;
 unsigned acount=which?45:192,bcount=which?54:36;
 tensor(0,3,acount,3,which?3:4,1,which?5:3,which?9:8,8);
 if(which)tensor(1,519,bcount,3,3,2,trans?3:9,trans?9:3,1);else tensor(1,519,bcount,3,4,3,3,2,2);
 tensor(2,1220,1,3,0,1,1,1,1);tensor(3,1221,3,3,1,3,1,1,1);tensor(4,1240,1,1,0,1,1,1,1);tensor(5,1256,3,1,1,3,1,1,1);tensor(6,1280,3,1,1,3,1,1,1);
 if(which)tensor(7,2053,batches*m*n,1,3,2,5,3,1);else tensor(7,2053,m*n,1,4,1,3,4,4);
 for(unsigned i=0;i<acount;++i)memory[3+i]=(uint8_t)(i*37+(i>>3)+13);
 for(unsigned i=0;i<bcount;++i)memory[519+i]=(uint8_t)(i*17+(i>>2)+7);
 memory[1220]=(uint8_t)-128;for(unsigned i=0;i<3;++i)memory[1221+i]=127;
 float scale_a=0.00125f,scales[3]={0.03125f,0.0625f,0.09375f},bias[3]={-0.125f,0,0.125f};
 memcpy(memory+1240,&scale_a,4);memcpy(memory+1256,scales,12);memcpy(memory+1280,bias,12);memcpy(expected,memory,sizeof(expected));
 job=(SwinJob){.batches=(int32_t)batches,.m=(int32_t)m,.n=(int32_t)n,.k=(int32_t)k,.araw=0,.ascale=4,.azero=2,.braw=1,.bscale=5,.bzero=3,.baxis=1,.trans_b=(int32_t)trans};
 node.op=which?19:18;node.executor=3;node.inputs=3;node.in[0]=0;node.in[1]=1;node.in[2]=6;node.output=7;node.job=0;
 node.attr[0]=2;node.attr[1]=2;node.attr[6]=1;node.attr[7]=1;
 for(unsigned batch=0;batch<batches;++batch)for(unsigned row=0;row<m;++row)for(unsigned col=0;col<n;++col){
  int32_t sum=0;
  for(unsigned inner=0;inner<k;++inner){
   unsigned ai=which?row*k+inner:(inner/4)*64+(row/4*2+(inner%4)/2)*8+row%4*2+inner%2;
   unsigned bi=which?batch*k*n+(trans?col*k+inner:inner*n+col):col*k+inner;
   sum+=((int)(int8_t)memory[3+ai]+128)*((int)(int8_t)memory[519+bi]-127);
  }
  float y=(float)sum*scale_a;y=y*scales[col];y=y+bias[col];unsigned oi=which?(batch*m+row)*n+col:col*m+row;memcpy(expected+2053+oi*4,&y,4);
 }
 AccelDispatch d={.timeout=2000000};AccelMacDispatch md={.dispatch=&d,.k_rows=128};uint32_t cc=0,dd=0;
 for(unsigned batch=0;batch<batches;++batch){
  int status=accel_plan_mac(&graph,0,batch,(uint32_t)(uintptr_t)(memory+4096),accel_emit_mac,&md,&cc,&dd);if(status){mac_plan_failure=(uint32_t)status;return 0x280+which;}
  status=accel_plan_post(&graph,0,batch,cc,dd,accel_emit_planned,&d);if(status){mac_plan_failure=(uint32_t)status;return 0x290+which;}
 }
 mac_plan_commands+=d.commands;mac_plan_engine_cycles+=d.engine_cycles;mac_plan_streamed_bytes+=d.streamed_bytes;
 if(d.commands!=4||d.streamed_bytes!=(which?240:768))return 0x2a0+which;
 unsigned c=cc-(uint32_t)(uintptr_t)memory,f=dd-(uint32_t)(uintptr_t)memory;
 for(unsigned i=0;i<8192;++i){
  if(i>=c&&i<c+m*n*4)continue;if(!which&&((i>=4096&&i<4096+m*k)||(i>=f&&i<f+m*n*4)))continue;
  uint8_t want=i<4096?expected[i]:0xa5;if(memory[i]!=want){mac_plan_failure=i+1;return 0x2b0+which;}
 }
 return 0;
}
int main(void){
 header=(SwinHeader){.nodes=1,.tensors=8,.jobs=1,.constant_base=(uint32_t)(uintptr_t)memory,.arena_base=(uint32_t)(uintptr_t)memory};
 graph=(SwinGraph){.h=&header,.t=tensors,.n=&node,.j=&job,.constants=memory,.arena=memory};
 for(unsigned i=0;i<3;++i){mac_plan_stage=i+1;int status=run(i);if(status)return status;}
 __asm__ volatile("fence iorw,iorw":::"memory");return 0;
}
