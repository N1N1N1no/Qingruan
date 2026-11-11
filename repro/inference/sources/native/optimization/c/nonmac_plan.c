/* Address/command lowering only. Original tensor ABI and operand order remain. */
#include "nonmac_plan.h"
#include "aux_plan.h"
#include <string.h>
static uint32_t bytes(uint32_t t){return t==1||t==6?4:t==7?8:1;}
static uint32_t address(SwinGraph *g,uint32_t id){const SwinTensor *t=g->t+id;return(t->storage==0?g->h->constant_base:g->h->arena_base)+t->offset;}
static uint32_t product(const int32_t *d,uint32_t b,uint32_t e){uint32_t p=1;for(;b<e;++b)p*=(uint32_t)d[b];return p;}
static uint32_t broadcast(uint32_t flat,const SwinTensor *o,const SwinTensor *s){uint32_t offset=0,stride=1;for(int d=(int)o->rank-1,k=(int)s->rank-1;d>=0;--d,--k){uint32_t q=flat%(uint32_t)o->dims[d];flat/=(uint32_t)o->dims[d];if(k>=0){if(s->dims[k]!=1)offset+=q*stride;stride*=(uint32_t)s->dims[k];}}return offset;}
static void patch(AccelPlannedCommand *c,unsigned slot,uint32_t addr){c->patch_mask|=1u<<slot;c->patch_address[slot]=addr;}
static int binary(SwinGraph *g,const SwinNode *n,AccelPlanEmit emit,void *ctx){
 const SwinTensor *o=g->t+n->output,*a=g->t+n->in[0],*b=g->t+n->in[1];
 if(a->dtype!=1||b->dtype!=1||o->dtype!=1)return -201;
 uint32_t inner=1;int d=(int)o->rank-1,ai=(int)a->rank-1,bi=(int)b->rank-1;
 int immediate=b->elements==1;
 for(;d>=0;--d,--ai,--bi){
  uint32_t ad=ai>=0?(uint32_t)a->dims[ai]:1,bd=bi>=0?(uint32_t)b->dims[bi]:1,od=(uint32_t)o->dims[d];
  if(ad!=od||(!immediate&&bd!=od))break;inner*=od;
 }
 uint32_t total_rows=o->elements/inner,row=0;
 while(row<total_rows){
  uint32_t ao=broadcast(row*inner,o,a),bo=broadcast(row*inner,o,b),ar=0,br=0,rows=1;
  if(inner<=65535&&row+1<total_rows){
   uint32_t an=broadcast((row+1)*inner,o,a),bn=broadcast((row+1)*inner,o,b);
   if(an>=ao&&bn>=bo){ar=an-ao;br=bn-bo;rows=2;
    while(row+rows<total_rows&&broadcast((row+rows)*inner,o,a)==ao+rows*ar&&broadcast((row+rows)*inner,o,b)==bo+rows*br)++rows;
   }
  }
  for(uint32_t start=0;start<inner;){
   uint32_t count=inner-start;if(count>65535)count=65535;
   AccelPlannedCommand c={0};c.opcode=7;
   c.args[0]=address(g,n->in[0])+(ao+start)*4;c.args[1]=address(g,n->in[1])+(bo+(immediate?0:start))*4;
   c.args[3]=address(g,n->output)+(row*inner+start)*4;c.args[4]=count;c.args[5]=rows;
   c.args[6]=ar*4;c.args[7]=br*4;c.args[9]=inner*4;c.args[10]=n->op-9;
   if(immediate){c.args[11]=1;patch(&c,13,address(g,n->in[1]));}
   int status=emit(ctx,&c);if(status)return status;start+=count;
  }
  row+=rows;
 }
 return 0;
}
int accel_plan_node(SwinGraph *g,uint32_t index,AccelPlanEmit emit,void *ctx){
 if(index>=g->h->nodes||!emit)return -200;const SwinNode *n=g->n+index;
 if(n->executor!=1)return 1;
 if(n->op>=9&&n->op<=11)return binary(g,n,emit,ctx);
 const SwinTensor *a=g->t+n->in[0],*o=g->t+n->output;AccelPlannedCommand c={0};
 c.args[0]=address(g,n->in[0]);c.args[1]=address(g,n->output);
 if(n->op==5){
  uint32_t strides[8],s=bytes(a->dtype),row=s,rank=o->rank;for(int j=(int)a->rank-1;j>=0;--j){strides[j]=s;s*=(uint32_t)a->dims[j];}
  while(rank&&((uint32_t)o->dims[rank-1]==1||strides[n->attr[rank-1]]==row)){row*=(uint32_t)o->dims[rank-1];--rank;}
  if(rank>4)return -202;c.opcode=8;c.args[2]=row;c.args[3]=rank?rank:1;
  for(uint32_t j=0;j<rank;++j){c.args[4+j]=(uint32_t)o->dims[j];c.args[8+j]=strides[n->attr[j]];}
  if(!rank)c.args[4]=1;return emit(ctx,&c);
 }
 if(n->op==13||n->op==14){
  int axis=n->attr[0];if(axis<0)axis+=(int)a->rank;if(axis<0||(uint32_t)axis>=a->rank)return -203;
  uint32_t inner=product(a->dims,(uint32_t)axis,a->rank),rows=a->elements/inner;
  if(n->op==14){if((uint32_t)axis+1!=a->rank)return -204;c.opcode=5;c.args[2]=inner;c.args[3]=rows;c.args[4]=inner*4;c.args[5]=inner*4;}
  else{c.opcode=6;c.args[2]=address(g,n->in[1]);c.args[3]=address(g,n->in[2]);c.args[4]=inner;c.args[5]=rows;c.args[6]=inner*4;c.args[7]=inner*4;memcpy(c.args+8,n->f,4);}
  return emit(ctx,&c);
 }
 if(n->op==17){
  const SwinTensor *scale=g->t+n->in[1],*zero=g->t+n->in[2];const uint8_t *zp=swin_tensor_data(g,n->in[2]);
  if(o->dtype!=1||scale->dtype!=1||!zp)return -205;
  if(a->dtype==6){for(uint32_t k=0;k<zero->elements*bytes(zero->dtype);++k)if(zp[k])return -206;if(scale->elements!=a->elements)return -207;}
  else if((a->dtype!=2&&a->dtype!=3)||scale->elements!=1||zero->elements!=1)return -208;
  for(uint32_t start=0;start<o->elements;){uint32_t count=o->elements-start;if(count>65535)count=65535;
   memset(&c,0,sizeof(c));c.opcode=7;c.args[0]=address(g,n->in[0])+start*bytes(a->dtype);c.args[1]=address(g,n->in[1])+(scale->elements==1?0:start*4);c.args[3]=address(g,n->output)+start*4;c.args[4]=count;c.args[5]=1;c.args[9]=count*4;c.args[10]=a->dtype==3?3:a->dtype==2?4:5;
   if(a->dtype!=6){c.args[11]=1;c.args[12]=zp[0];patch(&c,13,address(g,n->in[1]));}
   int status=emit(ctx,&c);if(status)return status;start+=count;
  }return 0;
 }
 return accel_plan_aux(g,index,0x83000000u,emit,ctx);
}
int accel_plan_post(SwinGraph *g,uint32_t index,uint32_t batch,uint32_t accumulator,uint32_t temporary,AccelPlanEmit emit,void *ctx){
 if(index>=g->h->nodes||!emit)return -210;const SwinNode *n=g->n+index;
 if(n->executor!=3||n->job<0||(uint32_t)n->job>=g->h->jobs)return -211;
 const SwinJob *j=g->j+n->job;const SwinTensor *bs=g->t+j->bscale;
 if(batch>=(uint32_t)j->batches||j->n<=0||j->n>65535||j->m<=0||g->t[j->ascale].elements!=1||(bs->elements!=1&&bs->elements!=(uint32_t)j->n))return -212;
 AccelPlannedCommand c={0};c.opcode=7;c.args[0]=accumulator;c.args[1]=address(g,j->bscale);c.args[3]=n->op==18?temporary:address(g,n->output)+batch*(uint32_t)j->m*(uint32_t)j->n*4;
 c.args[4]=(uint32_t)j->n;c.args[5]=(uint32_t)j->m;c.args[6]=(uint32_t)j->n*4;c.args[9]=(uint32_t)j->n*4;c.args[10]=n->inputs>2?7:6;patch(&c,13,address(g,j->ascale));
 if(bs->elements==1){c.args[11]|=1;patch(&c,14,address(g,j->bscale));}
 if(n->inputs>2){if(g->t[n->in[2]].elements!=(uint32_t)j->n)return -213;c.args[2]=address(g,n->in[2]);}
 int status=emit(ctx,&c);if(status||n->op!=18)return status;
 /* Conv's existing output_index maps [position,channel] to NCHW. */
 const SwinTensor *o=g->t+n->output;if(o->rank!=4||o->dims[0]!=1||batch!=0)return -214;
 memset(&c,0,sizeof(c));c.opcode=8;c.args[0]=temporary;c.args[1]=address(g,n->output);c.args[2]=4;c.args[3]=2;c.args[4]=(uint32_t)j->n;c.args[5]=(uint32_t)j->m;c.args[8]=4;c.args[9]=(uint32_t)j->n*4;
 return emit(ctx,&c);
}
