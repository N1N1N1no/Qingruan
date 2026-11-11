#include "mac_plan.h"
#include <string.h>
static uint32_t address(SwinGraph *g,uint32_t id){const SwinTensor *t=g->t+id;return(t->storage==0?g->h->constant_base:g->h->arena_base)+t->offset;}
static int zero_point(SwinGraph *g,int32_t id,int *value){
 if(id<0||(uint32_t)id>=g->h->tensors)return -230;const SwinTensor *t=g->t+id;const void *data=swin_tensor_data(g,(uint32_t)id);
 if(!data||!t->elements||(t->dtype!=2&&t->dtype!=3))return -231;
 int z=t->dtype==3?((const int8_t *)data)[0]:((const uint8_t *)data)[0];if(z< -128||z>127)return -232;
 for(uint32_t i=1;i<t->elements;++i){int x=t->dtype==3?((const int8_t *)data)[i]:((const uint8_t *)data)[i];if(x!=z)return -233;}
 *value=z;return 0;
}
static uint32_t batch_offset(const SwinTensor *t,const SwinTensor *out,uint32_t batch){
 uint32_t result=0,stride=(uint32_t)t->dims[t->rank-2]*(uint32_t)t->dims[t->rank-1];
 for(int s=(int)t->rank-3,d=(int)out->rank-3;d>=0;--s,--d){uint32_t c=batch%(uint32_t)out->dims[d];batch/=(uint32_t)out->dims[d];if(s>=0){if(t->dims[s]!=1)result+=c*stride;stride*=(uint32_t)t->dims[s];}}
 return result;
}
int accel_plan_mac(SwinGraph *g,uint32_t index,uint32_t batch,uint32_t workspace,AccelPlanEmit emit,void *ctx,uint32_t *accumulator,uint32_t *temporary){
 if(!emit||!accumulator||!temporary||index>=g->h->nodes)return -234;const SwinNode *n=g->n+index;
 if(n->executor!=3||n->job<0||(uint32_t)n->job>=g->h->jobs)return -235;const SwinJob *j=g->j+n->job;
 if(j->m<=0||j->n<=0||j->k<=0||batch>=(uint32_t)j->batches||j->araw<0||j->braw<0||(uint32_t)j->araw>=g->h->tensors||(uint32_t)j->braw>=g->h->tensors)return -236;
 const SwinTensor *a=g->t+j->araw,*b=g->t+j->braw,*out=g->t+n->output;
 if(a->dtype!=3||b->dtype!=3||a->storage>1||b->storage>1||a->rank<2||b->rank<2||out->dtype!=1||out->storage!=1)return -237;
 int az,bz,status=zero_point(g,j->azero,&az);if(status)return status;status=zero_point(g,j->bzero,&bz);if(status)return status;
 uint64_t aa=address(g,(uint32_t)j->araw),bb=address(g,(uint32_t)j->braw),cc=workspace;uint32_t trans=(uint32_t)j->trans_b;
 AccelPlannedCommand reorder={0};int im2col=n->op==18;
 if(im2col){
  if(a->rank!=4||b->rank!=4||out->rank!=4||a->dims[0]!=1||out->dims[0]!=1||batch||j->batches!=1||n->attr[0]<=0||n->attr[1]<=0||n->attr[6]<=0||n->attr[7]!=1)return -238;
  for(unsigned p=2;p<6;++p)if(n->attr[p])return -239;
  uint32_t ih=(uint32_t)a->dims[2],iw=(uint32_t)a->dims[3],ic=(uint32_t)a->dims[1],kh=(uint32_t)b->dims[2],kw=(uint32_t)b->dims[3],oh=(uint32_t)out->dims[2],ow=(uint32_t)out->dims[3];
  if((uint32_t)j->m!=oh*ow||(uint32_t)j->n!=(uint32_t)b->dims[0]||(uint32_t)j->k!=ic*kh*kw||
     (uint64_t)(oh-1)*(uint32_t)n->attr[0]+(uint64_t)(kh-1)*(uint32_t)n->attr[6]>=ih||(uint64_t)(ow-1)*(uint32_t)n->attr[1]+kw>iw)return -240;
  reorder.opcode=8;reorder.args[0]=(uint32_t)aa;reorder.args[1]=workspace;reorder.args[2]=kw;reorder.args[3]=4;
  reorder.args[4]=oh;reorder.args[5]=ow;reorder.args[6]=ic;reorder.args[7]=kh;
  reorder.args[8]=iw*(uint32_t)n->attr[0];reorder.args[9]=(uint32_t)n->attr[1];reorder.args[10]=ih*iw;reorder.args[11]=iw*(uint32_t)n->attr[6];
  aa=workspace;cc=((uint64_t)workspace+(uint64_t)j->m*j->k+63)&~63ull;trans=1;
 }else{
  uint32_t ao=j->batches==1?0:batch_offset(a,out,batch),bo=j->batches==1?0:batch_offset(b,out,batch);
  if((uint64_t)ao+(uint64_t)j->m*j->k>a->elements||(uint64_t)bo+(uint64_t)j->k*j->n>b->elements)return -241;aa+=ao;bb+=bo;
 }
 uint64_t dd=(cc+(uint64_t)j->m*j->n*4+63)&~63ull,end=dd+(uint64_t)j->m*j->n*4;
 uint64_t oa=address(g,n->output),oe=oa+(uint64_t)out->elements*4,ia=address(g,j->araw),ib=address(g,j->braw);
 if(oa<0x80000000ull||oe>workspace||(oa<ia+a->elements&&ia<oe)||(oa<ib+b->elements&&ib<oe))return -246;
 if(workspace<0x80000000u||(workspace&63)||end>0x83fff000ull||trans>1||address(g,j->araw)<0x80000000u||address(g,j->braw)<0x80000000u||
    (uint64_t)address(g,j->araw)+a->elements>workspace||(uint64_t)address(g,j->braw)+b->elements>workspace)return -242;
 if(im2col){status=emit(ctx,&reorder);if(status)return status;}
 AccelPlannedCommand c={0};c.opcode=3;c.args[0]=(uint32_t)aa;c.args[1]=(uint32_t)bb;c.args[2]=(uint32_t)cc;c.args[3]=(uint32_t)j->m;c.args[4]=(uint32_t)j->n;c.args[5]=(uint32_t)j->k;c.args[6]=(uint8_t)az;c.args[7]=(uint8_t)bz;c.args[8]=trans;
 status=emit(ctx,&c);if(status)return status;*accumulator=(uint32_t)cc;*temporary=(uint32_t)dd;return 0;
}
