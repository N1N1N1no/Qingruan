/* Standalone operators only: Slice, Concat and real 768x49 AvgPool shape. */
#include "aux_plan.h"
#include "nonmac_dispatch.h"
#include <string.h>
#define WORDS 100000u
#define MARKER 0xa55ac33cu
static volatile union{uint32_t u[WORDS];float f[WORDS];}memory __attribute__((aligned(64)));
static union{uint32_t u[50000];float f[50000];}expected __attribute__((aligned(64)));
static SwinHeader header;static SwinTensor tensors[8];static SwinNode node;static SwinGraph graph;
volatile uint32_t aux_stage,aux_failure,aux_commands,aux_out_lo,aux_out_hi,aux_work_lo,aux_work_hi;
volatile uint64_t aux_streamed_bytes,aux_kernel_cycles[2][2];
volatile uint32_t aux_pool_results[2][3];
static uint32_t bits(float f){union{float f;uint32_t u;}v={.f=f};return v.u;}
void *swin_tensor_data(SwinGraph *g,uint32_t id){const SwinTensor *t=g->t+id;return t->storage==0?(void *)(g->constants+t->offset):t->storage==1?g->arena+t->offset:0;}
static void tensor(unsigned id,unsigned offset,unsigned count,unsigned type,unsigned rank,int a,int b,int c,int d){tensors[id]=(SwinTensor){.offset=offset,.elements=count,.dtype=type,.rank=rank,.storage=type==7?0:1,.dims={a,b,c,d}};}
static void put64(unsigned offset,int64_t value){memcpy((void *)((volatile uint8_t *)&memory+offset),&value,8);}
static uint32_t sample(unsigned channel,unsigned at){
 uint32_t v=(channel*2654435761u+at*2246822519u)^0x61294e3bu;
 if(channel%101==100)return 0x80000000u; // Sum starts at +0, even for an all -0 row.
 if(channel%11==0&&at<2)return(at?0xdf123456u:0x5f123456u);
 if((channel+at)%29==0)return at%2?0x80000000u:0;
 if((channel+at)%31==0)return v&0x807fffffu;
 return(v&0x807fffffu)|((112+(v%17))<<23);
}
static int run(unsigned which){
 unsigned pool=which>=3,large=which==4,rows=large?768:5,inner=large?49:7;
 unsigned size=large?400000:16384,workspace=large?200000:4096,source=64,destination=large?160064:2052;
 for(unsigned i=0;i<size/4;++i)memory.u[i]=MARKER;memset(tensors,0,sizeof(tensors));memset(&node,0,sizeof(node));node.executor=1;
 if(which==0){
  tensor(0,source,120,1,4,1,5,6,4);tensor(1,640,2,7,1,2,1,1,1);tensor(2,672,2,7,1,2,1,1,1);tensor(3,704,2,7,1,2,1,1,1);tensor(4,736,2,7,1,2,1,1,1);tensor(5,destination,24,1,4,1,2,3,4);
  for(unsigned i=0;i<120;++i)memory.u[source/4+i]=sample(i/4,i%4);
  put64(640,-4);put64(648,1);put64(672,5);put64(680,6);put64(704,1);put64(712,2);put64(736,2);put64(744,2);
  node.op=6;node.inputs=5;for(unsigned i=0;i<5;++i)node.in[i]=(int32_t)i;node.output=5;
 }else if(which<3){
  unsigned inputs=which==1?2:4;node.op=8;node.inputs=inputs;node.attr[0]=which==1?1:-1;node.output=inputs;
  for(unsigned p=0;p<inputs;++p){
   unsigned count=which==1?(p?12:24):12,offset=source+p*256;
   tensor(p,offset,count,1,4,1,which==1?(p?1:2):2,3,which==1?4:2);node.in[p]=(int32_t)p;
   for(unsigned i=0;i<count;++i)memory.u[offset/4+i]=sample(p*31+i,i%7);
  }
  if(which==1)tensor(inputs,destination,36,1,4,1,3,3,4);else tensor(inputs,destination,48,1,4,1,2,3,8);
 }else{
  tensor(0,source,rows*inner,1,3,1,(int)rows,(int)inner,1);tensor(1,destination,rows,1,3,1,(int)rows,1,1);node.op=15;node.inputs=1;node.in[0]=0;node.output=1;
  for(unsigned row=0;row<rows;++row)for(unsigned i=0;i<inner;++i)memory.u[source/4+row*inner+i]=sample(row,i);
 }
 memcpy((void *)&expected,(const void *)&memory,workspace);
 if(which==0){
  for(unsigned h=0;h<2;++h)for(unsigned w=0;w<3;++w)for(unsigned c=0;c<4;++c)expected.u[destination/4+(h*3+w)*4+c]=memory.u[source/4+((1+2*h)*6+1+2*w)*4+c];
 }else if(which==1){
  for(unsigned i=0;i<24;++i)expected.u[destination/4+i]=memory.u[source/4+i];for(unsigned i=0;i<12;++i)expected.u[destination/4+24+i]=memory.u[(source+256)/4+i];
 }else if(which==2){
  for(unsigned r=0;r<6;++r)for(unsigned p=0;p<4;++p)for(unsigned c=0;c<2;++c)expected.u[destination/4+r*8+p*2+c]=memory.u[(source+p*256)/4+r*2+c];
 }else{
  uint64_t begin=accel_cycle64();
  for(unsigned row=0;row<rows;++row){float sum=0;for(unsigned i=0;i<inner;++i)sum+=memory.f[source/4+row*inner+i];expected.f[destination/4+row]=sum/(float)inner;}
  aux_kernel_cycles[large][0]=accel_cycle64()-begin;
 }
 uint32_t base=(uint32_t)(uintptr_t)&memory,p0=workspace,p1=(p0+rows*4+63)&~63u,tr=(p1+rows*4+63)&~63u;
 aux_out_lo=base+destination;aux_out_hi=aux_out_lo+tensors[node.output].elements*4;aux_work_lo=pool?base+workspace:0;aux_work_hi=pool?base+tr+rows*inner*4:0;
 AccelDispatch d={.timeout=2000000};uint64_t begin=accel_cycle64();int status=accel_plan_aux(&graph,0,base+workspace,accel_emit_planned,&d);
 if(pool){aux_kernel_cycles[large][1]=accel_cycle64()-begin;aux_pool_results[large][0]=(uint32_t)d.engine_cycles;aux_pool_results[large][1]=(uint32_t)d.streamed_bytes;aux_pool_results[large][2]=d.last.error;}
 aux_commands+=d.commands;aux_streamed_bytes+=d.streamed_bytes;
 unsigned want_commands=which==0?1:which==1?2:which==2?4:inner+2,want_bytes=pool?rows*4*(inner*2+1):tensors[node.output].elements*4;
 if(status||d.commands!=want_commands||d.streamed_bytes!=want_bytes){aux_failure=(uint32_t)status;return 0x320+which;}
 for(unsigned i=0;i<size/4;++i){
  unsigned at=i*4;if(pool&&((at>=p0&&at<p0+rows*4)||(at>=p1&&at<p1+rows*4)||(at>=tr&&at<tr+rows*inner*4)))continue;
  uint32_t want=at<workspace?expected.u[i]:MARKER;if(memory.u[i]!=want){aux_failure=i+1;return 0x330+which;}
 }
 return 0;
}
int main(void){
 uint32_t base=(uint32_t)(uintptr_t)&memory;header=(SwinHeader){.tensors=8,.nodes=1,.constant_base=base,.arena_base=base};graph=(SwinGraph){.h=&header,.t=tensors,.n=&node,.constants=(const uint8_t *)&memory,.arena=(uint8_t *)&memory};
 for(unsigned i=0;i<5;++i){aux_stage=i+1;int status=run(i);if(status)return status;}
 __asm__ volatile("fence iorw,iorw":::"memory");return 0;
}
