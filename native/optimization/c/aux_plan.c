#include "aux_plan.h"
#include <string.h>
static uint32_t address(SwinGraph *g,uint32_t id){const SwinTensor *t=g->t+id;return(t->storage==0?g->h->constant_base:g->h->arena_base)+t->offset;}
static int tensor(SwinGraph *g,int32_t id,uint32_t workspace,int output,uint64_t *lo,uint64_t *hi){
 if(id<0||(uint32_t)id>=g->h->tensors)return -250;const SwinTensor *t=g->t+id;
 if(t->dtype!=1||t->storage>1||(output&&t->storage!=1)||!t->elements||t->rank>8)return -251;
 uint64_t count=1;for(uint32_t d=0;d<t->rank;++d){if(t->dims[d]<=0||count>0x1000000ull/(uint32_t)t->dims[d])return -252;count*=(uint32_t)t->dims[d];}
 if(count!=t->elements)return -253;*lo=address(g,(uint32_t)id);*hi=*lo+count*4;
 return *lo<0x80000000ull||*hi>workspace?-254:0;
}
static int input(SwinGraph *g,int32_t id,uint32_t workspace,uint64_t outlo,uint64_t outhi){
 uint64_t lo,hi;int status=tensor(g,id,workspace,0,&lo,&hi);if(status)return status;return lo<outhi&&outlo<hi?-255:0;
}
static int integer(SwinGraph *g,int32_t id,uint32_t index,int64_t *value){
 if(id<0||(uint32_t)id>=g->h->tensors)return -256;const SwinTensor *t=g->t+id;const uint8_t *p=swin_tensor_data(g,(uint32_t)id);
 if(!p||index>=t->elements)return -257;
 if(t->dtype==7){memcpy(value,p+index*8,8);return 0;}
 if(t->dtype==6){int32_t x;memcpy(&x,p+index*4,4);*value=x;return 0;}return -258;
}
static int emit_slice(SwinGraph *g,const SwinNode *n,uint32_t workspace,AccelPlanEmit emit,void *ctx,uint64_t outlo,uint64_t outhi){
 if(n->inputs<3)return -259;const SwinTensor *a=g->t+n->in[0],*o=g->t+n->output,*st=g->t+n->in[1];
 if(a->rank!=o->rank||!a->rank||st->elements>a->rank)return -260;
 int status=input(g,n->in[0],workspace,outlo,outhi);if(status)return status;
 int64_t starts[8]={0},steps[8]={1,1,1,1,1,1,1,1};
 for(uint32_t k=0;k<st->elements;++k){
  int64_t d=(int64_t)k,s,v;status=integer(g,n->in[1],k,&s);if(status)return status;
  if(n->inputs>3){status=integer(g,n->in[3],k,&d);if(status)return status;}if(d<0)d+=a->rank;if(d<0||(uint64_t)d>=a->rank)return -261;
  v=1;if(n->inputs>4){status=integer(g,n->in[4],k,&v);if(status)return status;}if(!v)return -262;if(v<0)return 1;
  int64_t dim=a->dims[d];if(s<0)s+=dim;if(s<0)s=0;if(s>dim)s=dim;starts[d]=s;steps[d]=v;
 }
 uint64_t offset=0,stride=4;uint32_t strides[8];
 for(int d=(int)a->rank-1;d>=0;--d){
  if(starts[d]<0||(uint64_t)starts[d]>=(uint32_t)a->dims[d]||(uint64_t)(o->dims[d]-1)>((uint64_t)a->dims[d]-1-(uint64_t)starts[d])/(uint64_t)steps[d])return -263;
  offset+=(uint64_t)starts[d]*stride;uint64_t step=o->dims[d]==1?0:stride*(uint64_t)steps[d];if(step>0x4000000ull)return 1;strides[d]=(uint32_t)step;stride*=(uint32_t)a->dims[d];
 }
 uint32_t rank=o->rank,row=4;while(rank&&(o->dims[rank-1]==1||strides[rank-1]==row)){row*=(uint32_t)o->dims[rank-1];--rank;}if(rank>4)return 1;
 AccelPlannedCommand c={0};c.opcode=8;c.args[0]=address(g,n->in[0])+(uint32_t)offset;c.args[1]=(uint32_t)outlo;c.args[2]=row;c.args[3]=rank?rank:1;
 for(uint32_t d=0;d<rank;++d){c.args[4+d]=(uint32_t)o->dims[d];c.args[8+d]=strides[d];}if(!rank)c.args[4]=1;
 return emit(ctx,&c);
}
static int emit_concat(SwinGraph *g,const SwinNode *n,uint32_t workspace,AccelPlanEmit emit,void *ctx,uint64_t outlo,uint64_t outhi){
 const SwinTensor *o=g->t+n->output;if(!n->inputs||n->inputs>8||!o->rank)return -264;int axis=n->attr[0];if(axis<0)axis+=(int)o->rank;if(axis<0||(uint32_t)axis>=o->rank)return -265;
 uint32_t inner=1,outer=1;for(uint32_t d=0;d<(uint32_t)axis;++d)outer*=(uint32_t)o->dims[d];for(uint32_t d=(uint32_t)axis+1;d<o->rank;++d)inner*=(uint32_t)o->dims[d];
 uint64_t channels=0;AccelPlannedCommand commands[8];memset(commands,0,sizeof(commands));
 for(uint32_t p=0;p<n->inputs;++p){
  int status=input(g,n->in[p],workspace,outlo,outhi);if(status)return status;const SwinTensor *t=g->t+n->in[p];if(t->rank!=o->rank)return -266;
  for(uint32_t d=0;d<o->rank;++d)if(d!=(uint32_t)axis&&t->dims[d]!=o->dims[d])return -267;
  uint32_t row=(uint32_t)t->dims[axis]*inner*4;AccelPlannedCommand *c=commands+p;c->opcode=1;c->args[0]=address(g,n->in[p]);c->args[1]=(uint32_t)(outlo+channels*inner*4);c->args[2]=row;c->args[3]=outer;c->args[4]=row;c->args[5]=(uint32_t)o->dims[axis]*inner*4;channels+=(uint32_t)t->dims[axis];
 }
 if(channels!=(uint32_t)o->dims[axis])return -268;
 for(uint32_t p=0;p<n->inputs;++p){int status=emit(ctx,commands+p);if(status)return status;}return 0;
}
static int emit_pool(SwinGraph *g,const SwinNode *n,uint32_t workspace,AccelPlanEmit emit,void *ctx,uint64_t outlo,uint64_t outhi){
 int status=input(g,n->in[0],workspace,outlo,outhi);if(status)return status;const SwinTensor *a=g->t+n->in[0],*o=g->t+n->output;
 if(a->rank<3)return -269;uint32_t rows=o->elements;uint64_t inner=1;for(uint32_t d=2;d<a->rank;++d)inner*=(uint32_t)a->dims[d];
 if(!inner||inner*rows!=a->elements)return -270;if(rows>65535)return 1;
 uint64_t p0=workspace,p1=(p0+(uint64_t)rows*4+63)&~63ull,transposed=(p1+(uint64_t)rows*4+63)&~63ull,end=transposed+(uint64_t)a->elements*4;
 if((workspace&63)||end>0x83fff000ull)return -271;
 AccelPlannedCommand c={0};c.opcode=8;c.args[0]=address(g,n->in[0]);c.args[1]=(uint32_t)transposed;c.args[2]=4;c.args[3]=2;c.args[4]=(uint32_t)inner;c.args[5]=rows;c.args[8]=4;c.args[9]=(uint32_t)inner*4;
 status=emit(ctx,&c);if(status)return status;uint32_t previous=0;
 for(uint32_t step=0;step<(uint32_t)inner;++step){
  uint32_t destination=(uint32_t)(step%2?p1:p0);memset(&c,0,sizeof(c));c.opcode=7;c.args[0]=step?previous:(uint32_t)transposed;c.args[1]=(uint32_t)transposed+step*rows*4;c.args[3]=destination;c.args[4]=rows;c.args[5]=1;c.args[6]=rows*4;c.args[9]=rows*4;
  if(!step)c.args[11]=1; // +0 initialization preserves the CPU's first rounded add.
  status=emit(ctx,&c);if(status)return status;previous=destination;
 }
 memset(&c,0,sizeof(c));c.opcode=7;c.args[0]=previous;c.args[3]=(uint32_t)outlo;c.args[4]=rows;c.args[5]=1;c.args[6]=rows*4;c.args[9]=rows*4;c.args[10]=2;c.args[11]=1;float divisor=(float)inner;memcpy(c.args+13,&divisor,4);
 return emit(ctx,&c);
}
int accel_plan_aux(SwinGraph *g,uint32_t index,uint32_t workspace,AccelPlanEmit emit,void *ctx){
 if(index>=g->h->nodes||!emit)return -272;const SwinNode *n=g->n+index;const SwinTensor *o=g->t+n->output;
 if(n->executor!=1||o->dtype!=1||(n->op!=6&&n->op!=8&&n->op!=15))return 1;
 uint64_t lo,hi;int status=tensor(g,(int32_t)n->output,workspace,1,&lo,&hi);if(status)return status;
 if(n->op==6)return emit_slice(g,n,workspace,emit,ctx,lo,hi);
 if(n->op==8)return emit_concat(g,n,workspace,emit,ctx,lo,hi);
 return emit_pool(g,n,workspace,emit,ctx,lo,hi);
}
