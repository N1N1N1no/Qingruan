/* Synthetic module graph: no model/image inference. Tests runtime scalar patches. */
#include "nonmac_dispatch.h"
#include <math.h>
#include <string.h>
static uint8_t buffers[16][1536] __attribute__((aligned(64)));
static SwinHeader header;static SwinTensor tensors[16];static SwinNode nodes[7];static SwinJob job;static SwinGraph graph;
volatile uint32_t planner_stage,planner_failure,planner_commands;
volatile uint64_t planner_engine_cycles,planner_streamed_bytes;
static const unsigned starts[16]={3,5,7,1,5,3,1,3,5,3,1,5,3,1,5,5};
void *swin_tensor_data(SwinGraph *g,uint32_t id){const SwinTensor *t=g->t+id;return t->storage==0?(void *)(g->constants+t->offset):t->storage==1?g->arena+t->offset:0;}
static void store(unsigned id,unsigned i,float x){memcpy(buffers[id]+starts[id]+i*4,&x,4);}
static float load(unsigned id,unsigned i){float x;memcpy(&x,buffers[id]+starts[id]+i*4,4);return x;}
static void tensor(unsigned id,unsigned count,unsigned type,unsigned rank,unsigned x,unsigned y){
 tensors[id]=(SwinTensor){.offset=(uint32_t)(uintptr_t)(buffers[id]+starts[id])-0x80000000u,.elements=count,.dtype=type,.rank=rank,.storage=1,.dims={(int32_t)x,(int32_t)y}};
}
static int execute(unsigned stage){
 const unsigned targets[7]={2,4,7,8,11,14,15};unsigned output=targets[stage],count=tensors[output].elements;uint8_t expected[1536];memset(expected,0xa5,sizeof(expected));
 if(stage==2){
  for(unsigned row=0;row<3;++row){float sum=0,ss=0;for(unsigned i=0;i<17;++i){float x=load(4,row*17+i);sum+=x;ss+=x*x;}float mean=sum/17.0f,var=ss/17.0f-mean*mean,inv=1.0f/sqrtf(fmaxf(var,0.0f)+1e-5f);
   for(unsigned i=0;i<17;++i){float y=(load(4,row*17+i)-mean)*inv*load(5,i)+load(6,i);memcpy(expected+starts[output]+(row*17+i)*4,&y,4);}
  }
 }else if(stage==3){
  for(unsigned row=0;row<3;++row){float maximum=-INFINITY,sum=0,e[17];for(unsigned i=0;i<17;++i)maximum=fmaxf(maximum,load(7,row*17+i));for(unsigned i=0;i<17;++i){e[i]=expf(load(7,row*17+i)-maximum);sum+=e[i];}for(unsigned i=0;i<17;++i){float y=e[i]/sum;memcpy(expected+starts[output]+(row*17+i)*4,&y,4);}}
 }else for(unsigned i=0;i<count;++i){float y=0;
  if(stage==0)y=load(0,i%17)+load(1,i);
  if(stage==1)y=load(2,i)*load(3,0);
  if(stage==4)y=(float)((int)(int8_t)buffers[9][starts[9]+i]+37)*load(3,0);
  if(stage==5){int32_t a;memcpy(&a,buffers[12]+starts[12]+i*4,4);y=(float)a*load(13,0);y=y*load(5,i%17);y=y+load(6,i%17);}
  if(stage==6)y=load(14,(i%3)*17+i/3);
  memcpy(expected+starts[output]+i*4,&y,4);
 }
 AccelDispatch d={.timeout=2000000};int status;
 if(stage==5)status=accel_plan_post(&graph,5,0,(uint32_t)(uintptr_t)(buffers[12]+starts[12]),0,accel_emit_planned,&d);
 else status=accel_plan_node(&graph,stage,accel_emit_planned,&d);
 planner_commands+=d.commands;planner_engine_cycles+=d.engine_cycles;planner_streamed_bytes+=d.streamed_bytes;
 if(status||d.commands!=1||d.streamed_bytes!=count*4){planner_failure=(uint32_t)status;return 0x200+stage;}
 for(unsigned i=0;i<1536;++i)if(buffers[output][i]!=expected[i]){planner_failure=i+1;return 0x210+stage;}
 return 0;
}
int main(void){
 memset(buffers,0xa5,sizeof(buffers));header.arena_base=0x80000000u;header.nodes=7;header.tensors=16;header.jobs=1;
 graph=(SwinGraph){.h=&header,.t=tensors,.n=nodes,.j=&job,.arena=(uint8_t *)(uintptr_t)0x80000000u};
 for(unsigned id=0;id<16;++id)tensor(id,51,1,2,3,17);
 for(unsigned id=0;id<7;++id)nodes[id].executor=1;
 tensor(0,17,1,1,17,1);tensor(3,1,1,0,1,1);tensor(5,17,1,1,17,1);tensor(6,17,1,1,17,1);tensor(9,256,3,1,256,1);tensor(10,1,3,0,1,1);tensor(11,256,1,1,256,1);tensor(12,51,6,2,3,17);tensor(13,1,1,0,1,1);tensor(15,51,1,2,17,3);
 for(unsigned i=0;i<17;++i){store(0,i,((int)i-8)*0.3125f);store(5,i,((int)(i%7)-3)*0.125f);store(6,i,((int)(i%11)-5)*0.03125f);}
 for(unsigned i=0;i<51;++i){store(1,i,((int)(i%23)-11)*0.125f);int32_t a=(int32_t)(i*1234567)-18000000;memcpy(buffers[12]+starts[12]+i*4,&a,4);}
 for(unsigned i=0;i<256;++i)buffers[9][starts[9]+i]=(uint8_t)i;buffers[10][starts[10]]=(uint8_t)-37;store(3,0,0.0625f);store(13,0,0.00125f);
 nodes[0].op=9;nodes[0].output=2;nodes[0].inputs=2;nodes[0].in[0]=0;nodes[0].in[1]=1;
 nodes[1].op=10;nodes[1].output=4;nodes[1].inputs=2;nodes[1].in[0]=2;nodes[1].in[1]=3;
 nodes[2].op=13;nodes[2].output=7;nodes[2].inputs=3;nodes[2].in[0]=4;nodes[2].in[1]=5;nodes[2].in[2]=6;nodes[2].attr[0]=1;nodes[2].f[0]=1e-5f;
 nodes[3].op=14;nodes[3].output=8;nodes[3].inputs=1;nodes[3].in[0]=7;nodes[3].attr[0]=-1;
 nodes[4].op=17;nodes[4].output=11;nodes[4].inputs=3;nodes[4].in[0]=9;nodes[4].in[1]=3;nodes[4].in[2]=10;
 nodes[5].op=20;nodes[5].executor=3;nodes[5].output=14;nodes[5].inputs=3;nodes[5].in[2]=6;nodes[5].job=0;
 job=(SwinJob){.batches=1,.m=3,.n=17,.k=48,.ascale=13,.bscale=5};
 nodes[6].op=5;nodes[6].output=15;nodes[6].inputs=1;nodes[6].in[0]=14;nodes[6].attr[0]=1;nodes[6].attr[1]=0;
 for(unsigned i=0;i<7;++i){planner_stage=i+1;int status=execute(i);if(status)return status;}
 __asm__ volatile("fence iorw,iorw":::"memory");return 0;
}
