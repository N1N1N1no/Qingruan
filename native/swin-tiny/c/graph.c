#include "graph.h"
#include <math.h>
#include <limits.h>
#include <string.h>
#ifdef SWIN_HOST
#include <stdlib.h>
#endif
_Static_assert(sizeof(SwinHeader)==64,"header ABI");
_Static_assert(sizeof(SwinTensor)==56,"tensor ABI");
_Static_assert(sizeof(SwinNode)==132,"node ABI");
_Static_assert(sizeof(SwinJob)==48,"job ABI");
enum {CONSTANT,IDENTITY,RESHAPE,FLATTEN,SHAPE,TRANSPOSE,SLICE,GATHER,CONCAT,ADD,MUL,DIV,ERF,
      LAYERNORM,SOFTMAX,AVGPOOL,QUANT,DEQUANT,CONV,MATMUL,GEMM};
static uint32_t width(uint32_t dtype) { return dtype==1||dtype==6 ? 4 : dtype==7 ? 8 : 1; }
static uint32_t product(const int32_t *d,uint32_t begin,uint32_t end) {
    uint32_t p=1;for (uint32_t i=begin;i<end;++i)p*=(uint32_t)d[i];return p;
}
void *swin_tensor_data(SwinGraph *g,uint32_t id) {
    const SwinTensor *t=g->t+id;
    return t->storage==0 ? (void *)(g->constants+t->offset) : t->storage==1 ? g->arena+t->offset : 0;
}
static int64_t integer(const void *p,uint32_t type,uint32_t i) {
    if(type==3)return ((const int8_t *)p)[i];
    if(type==2)return ((const uint8_t *)p)[i];
    if(type==6)return ((const int32_t *)p)[i];
    return ((const int64_t *)p)[i];
}
static int axis(int a,uint32_t rank) { return a<0 ? a+(int)rank : a; }
/* Flat coordinate in the broadcast output -> source coordinate. */
static uint32_t broadcast(uint32_t flat,const SwinTensor *out,const SwinTensor *src) {
    uint32_t offset=0,stride=1;
    for(int d=(int)out->rank-1,s=(int)src->rank-1;d>=0;--d,--s) {
        uint32_t c=flat%(uint32_t)out->dims[d];flat/=(uint32_t)out->dims[d];
        if(s>=0) { if(src->dims[s]!=1)offset+=c*stride;stride*=(uint32_t)src->dims[s]; }
    }
    return offset;
}
int swin_graph_init(SwinGraph *g,const void *image,const void *constants,void *arena,SwinPeBus *bus) {
    memset(g,0,sizeof(*g));g->h=image;
    if(g->h->magic!=0x4e495753||g->h->version!=1)return -1;
    g->t=(const SwinTensor *)((const uint8_t *)image+g->h->tensor_offset);
    g->n=(const SwinNode *)((const uint8_t *)image+g->h->node_offset);
    g->j=(const SwinJob *)((const uint8_t *)image+g->h->job_offset);
    g->constants=constants;g->arena=arena;g->bus=bus;
    for(uint32_t i=0;i<g->h->tensors;++i) {
        const SwinTensor *t=g->t+i;
        uint32_t limit=t->storage==0 ? g->h->constant_bytes : g->h->arena_bytes;
        if(t->rank>8||t->storage>2|| (t->storage<2 &&
           (uint64_t)t->offset+(uint64_t)t->elements*width(t->dtype)>limit))return -2;
    }
    return 0;
}
typedef struct { SwinGraph *g;const SwinNode *node;const SwinJob *job; } Mac;
static uint32_t batch_offset(const SwinTensor *t,const SwinTensor *out,uint32_t batch) {
    uint32_t result=0,stride=(uint32_t)t->dims[t->rank-2]*(uint32_t)t->dims[t->rank-1];
    for(int s=(int)t->rank-3,d=(int)out->rank-3;d>=0;--s,--d) {
        uint32_t c=batch%(uint32_t)out->dims[d];batch/=(uint32_t)out->dims[d];
        if(s>=0) { if(t->dims[s]!=1)result+=c*stride;stride*=(uint32_t)t->dims[s]; }
    }
    return result;
}
static int16_t read_a(void *ctx,uint32_t batch,uint32_t row,uint32_t k) {
    Mac *c=ctx;SwinGraph *g=c->g;const SwinNode *n=c->node;const SwinJob *j=c->job;
    const SwinTensor *t=g->t+j->araw;
    int32_t zero=(int32_t)integer(swin_tensor_data(g,j->azero),g->t[j->azero].dtype,0);
    uint32_t index;
    if(n->op==CONV) {
        const SwinTensor *b=g->t+j->braw,*o=g->t+n->output;
        uint32_t kh=(uint32_t)b->dims[2],kw=(uint32_t)b->dims[3],ocells=(uint32_t)o->dims[2]*(uint32_t)o->dims[3];
        uint32_t image=row/ocells,pos=row%ocells,channel=k/(kh*kw),term=k%(kh*kw);
        int32_t y=(int32_t)(pos/(uint32_t)o->dims[3])*n->attr[0]-n->attr[2]+(int32_t)(term/kw)*n->attr[6];
        int32_t x=(int32_t)(pos%(uint32_t)o->dims[3])*n->attr[1]-n->attr[3]+(int32_t)(term%kw)*n->attr[7];
        if(y<0||x<0||y>=t->dims[2]||x>=t->dims[3])return 0;
        index=((image*(uint32_t)t->dims[1]+channel)*(uint32_t)t->dims[2]+(uint32_t)y)*(uint32_t)t->dims[3]+(uint32_t)x;
    } else index=(j->batches==1 ? row*(uint32_t)j->k :
                           batch_offset(t,g->t+n->output,batch)+row*(uint32_t)j->k)+k;
    return (int16_t)(integer(swin_tensor_data(g,j->araw),t->dtype,index)-zero);
}
static int16_t read_b(void *ctx,uint32_t batch,uint32_t k,uint32_t col) {
    Mac *c=ctx;SwinGraph *g=c->g;const SwinNode *n=c->node;const SwinJob *j=c->job;
    const SwinTensor *t=g->t+j->braw,*z=g->t+j->bzero;
    int32_t zero=(int32_t)integer(swin_tensor_data(g,j->bzero),z->dtype,z->elements==1 ? 0 : col);
    uint32_t index;
    if(n->op==CONV||j->trans_b)index=col*(uint32_t)j->k+k;
    else index=(j->batches==1 ? 0 : batch_offset(t,g->t+n->output,batch))+k*(uint32_t)j->n+col;
    return (int16_t)(integer(swin_tensor_data(g,j->braw),t->dtype,index)-zero);
}
static uint32_t output_index(Mac *c,uint32_t batch,uint32_t row,uint32_t col) {
    if(c->node->op==CONV) {
        const SwinTensor *o=c->g->t+c->node->output;uint32_t cells=product(o->dims,2,o->rank);
        return ((row/cells)*(uint32_t)c->job->n+col)*cells+row%cells;
    }
    return (batch*(uint32_t)c->job->m+row)*(uint32_t)c->job->n+col;
}
static int32_t load_acc(void *ctx,uint32_t b,uint32_t m,uint32_t n) {
    Mac *c=ctx;int32_t v;memcpy(&v,(uint8_t *)swin_tensor_data(c->g,c->node->output)+4*output_index(c,b,m,n),4);return v;
}
static void store_acc(void *ctx,uint32_t b,uint32_t m,uint32_t n,int32_t v) {
    Mac *c=ctx;memcpy((uint8_t *)swin_tensor_data(c->g,c->node->output)+4*output_index(c,b,m,n),&v,4);
}
#ifdef SWIN_HOST
/* Independent, exact INT32 host executor. It never substitutes for PE RTL evidence. */
static int host_mac(Mac *c) {
    const SwinJob *j=c->job;int16_t *b=malloc((size_t)j->k*j->n*2);
    int32_t *sums=malloc((size_t)j->n*4);if(!b||!sums){free(b);free(sums);return -10;}
    for(uint32_t batch=0;batch<(uint32_t)j->batches;++batch) {
        for(uint32_t k=0;k<(uint32_t)j->k;++k)for(uint32_t n=0;n<(uint32_t)j->n;++n)b[k*j->n+n]=read_b(c,batch,k,n);
        for(uint32_t m=0;m<(uint32_t)j->m;++m) {
            memset(sums,0,(size_t)j->n*4);
            for(uint32_t k=0;k<(uint32_t)j->k;++k) {
                int32_t a=read_a(c,batch,m,k);const int16_t *v=b+k*j->n;
                for(uint32_t n=0;n<(uint32_t)j->n;++n)sums[n]+=a*(int32_t)v[n];
            }
            for(uint32_t n=0;n<(uint32_t)j->n;++n)store_acc(c,batch,m,n,sums[n]);
        }
    }
    free(b);free(sums);return 0;
}
#endif
static int mac(SwinGraph *g,const SwinNode *n) {
    if(n->job<0||(uint32_t)n->job>=g->h->jobs)return -11;
    const SwinJob *j=g->j+n->job;Mac c={g,n,j};
    SwinPeJob job={(uint32_t)j->batches,(uint32_t)j->m,(uint32_t)j->n,(uint32_t)j->k,
                   &c,&c,&c,read_a,read_b,load_acc,store_acc};
    int status;
#ifdef SWIN_HOST
    if(!g->bus)status=host_mac(&c);else
#endif
    status=swin_pe_gemm(g->bus,&job,&g->stats);
    if(status)return status;
    float *out=swin_tensor_data(g,n->output);
    const float *as=swin_tensor_data(g,j->ascale),*bs=swin_tensor_data(g,j->bscale);
    const SwinTensor *bscale=g->t+j->bscale;
    const float *bias=n->inputs>2 ? swin_tensor_data(g,n->in[2]) : 0;
    for(uint32_t batch=0;batch<(uint32_t)j->batches;++batch)for(uint32_t m=0;m<(uint32_t)j->m;++m)
        for(uint32_t col=0;col<(uint32_t)j->n;++col) {
            uint32_t index=output_index(&c,batch,m,col);int32_t sum=load_acc(&c,batch,m,col);
            float value=(float)sum*as[0]*bs[bscale->elements==1 ? 0 : col];
            out[index]=bias ? value+bias[col] : value;
        }
    ++g->executed_jobs;return 0;
}
int swin_graph_node(SwinGraph *g,uint32_t index) {
    if(index>=g->h->nodes)return -20;
    const SwinNode *n=g->n+index;const SwinTensor *o=g->t+n->output;
    if(n->executor==0||n->executor==2)return 0;
    if(n->executor==3)return mac(g,n);
    const SwinTensor *a=g->t+n->in[0];const void *ap=swin_tensor_data(g,n->in[0]);
    uint8_t *dst=swin_tensor_data(g,n->output);const float *af=ap;float *out=(float *)dst;
    uint32_t size=width(o->dtype);
    if(!ap||!dst)return -21;
    switch(n->op) {
    case IDENTITY:case RESHAPE:case FLATTEN:
        if(a->elements!=o->elements)return -22;
        memmove(dst,ap,o->elements*size);break;
    case SHAPE:
        for(uint32_t i=0;i<a->rank;++i)((int64_t *)dst)[i]=a->dims[i];break;
    case TRANSPOSE:
        for(uint32_t i=0;i<o->elements;++i) {
            uint32_t flat=i,offset=0,strides[8];strides[a->rank-1]=1;
            for(int k=(int)a->rank-2;k>=0;--k)strides[k]=strides[k+1]*(uint32_t)a->dims[k+1];
            for(int d=(int)o->rank-1;d>=0;--d){offset+=(flat%(uint32_t)o->dims[d])*strides[n->attr[d]];flat/=(uint32_t)o->dims[d];}
            memcpy(dst+i*size,(const uint8_t *)ap+offset*size,size);
        }break;
    case ADD:case MUL:case DIV: {
        const SwinTensor *b=g->t+n->in[1];const float *bf=swin_tensor_data(g,n->in[1]);
        for(uint32_t i=0;i<o->elements;++i){float x=af[broadcast(i,o,a)],y=bf[broadcast(i,o,b)];out[i]=n->op==ADD?x+y:n->op==MUL?x*y:x/y;}
        break; }
    case ERF:for(uint32_t i=0;i<o->elements;++i)out[i]=erff(af[i]);break;
    case QUANT:case DEQUANT: {
        const SwinTensor *s=g->t+n->in[1],*z=g->t+n->in[2];
        const float *scale=swin_tensor_data(g,n->in[1]);const void *zero=swin_tensor_data(g,n->in[2]);
        int d=axis(n->attr[0],a->rank);uint32_t inner=s->elements==1 ? 1 : product(a->dims,(uint32_t)d+1,a->rank);
        for(uint32_t i=0;i<o->elements;++i) {
            uint32_t channel=s->elements==1 ? 0 : i/inner%(uint32_t)a->dims[d];
            int64_t zp=integer(zero,z->dtype,channel);
            if(n->op==DEQUANT)out[i]=(float)(integer(ap,a->dtype,i)-zp)*scale[channel];
            else {
                /* nearbyintf obeys the default IEEE ties-to-even mode. */
                float value=nearbyintf(af[i]/scale[channel])+(float)zp;
                int low=o->dtype==3?-128:0,high=o->dtype==3?127:255;
                if(value<low)value=(float)low;if(value>high)value=(float)high;
                if(o->dtype==3)((int8_t *)dst)[i]=(int8_t)value;else dst[i]=(uint8_t)value;
            }
        }break; }
    case LAYERNORM: {
        int d=axis(n->attr[0],a->rank);uint32_t inner=product(a->dims,(uint32_t)d,a->rank);
        const float *scale=swin_tensor_data(g,n->in[1]),*bias=swin_tensor_data(g,n->in[2]);
        for(uint32_t start=0;start<a->elements;start+=inner) {
            float sum=0,ss=0;
            for(uint32_t i=0;i<inner;++i){float x=af[start+i];sum+=x;ss+=x*x;}
            float mean=sum/(float)inner,var=ss/(float)inner-mean*mean;
            float inverse=1.0f/sqrtf(fmaxf(var,0.0f)+n->f[0]);
            for(uint32_t i=0;i<inner;++i)out[start+i]=(af[start+i]-mean)*inverse*scale[i]+bias[i];
        }break; }
    case SOFTMAX: {
        int d=axis(n->attr[0],a->rank);uint32_t inner=product(a->dims,(uint32_t)d+1,a->rank),channels=(uint32_t)a->dims[d];
        uint32_t outer=a->elements/(inner*channels);
        for(uint32_t b=0;b<outer;++b)for(uint32_t t=0;t<inner;++t) {
            uint32_t base=b*channels*inner+t;float max=-INFINITY,sum=0;
            for(uint32_t j=0;j<channels;++j)max=fmaxf(max,af[base+j*inner]);
            for(uint32_t j=0;j<channels;++j){float e=expf(af[base+j*inner]-max);out[base+j*inner]=e;sum+=e;}
            for(uint32_t j=0;j<channels;++j)out[base+j*inner]/=sum;
        }break; }
    case AVGPOOL: {
        uint32_t inner=product(a->dims,2,a->rank);
        for(uint32_t i=0;i<o->elements;++i){float s=0;for(uint32_t k=0;k<inner;++k)s+=af[i*inner+k];out[i]=s/(float)inner;}break; }
    case GATHER: {
        int d=axis(n->attr[0],a->rank);const SwinTensor *it=g->t+n->in[1];const void *indices=swin_tensor_data(g,n->in[1]);
        uint32_t inner=product(a->dims,(uint32_t)d+1,a->rank),outer=product(a->dims,0,(uint32_t)d),channels=(uint32_t)a->dims[d];
        for(uint32_t b=0;b<outer;++b)for(uint32_t k=0;k<it->elements;++k) {
            int64_t ix=integer(indices,it->dtype,k);if(ix<0)ix+=channels;if(ix<0||ix>=channels)return -23;
            memcpy(dst+(b*it->elements+k)*inner*size,(const uint8_t *)ap+(b*channels+(uint32_t)ix)*inner*size,inner*size);
        }break; }
    case CONCAT: {
        int d=axis(n->attr[0],o->rank);uint32_t inner=product(o->dims,(uint32_t)d+1,o->rank),outer=product(o->dims,0,(uint32_t)d);
        for(uint32_t b=0;b<outer;++b){uint32_t channel=0;
            for(uint32_t k=0;k<n->inputs;++k){const SwinTensor *t=g->t+n->in[k];uint32_t count=(uint32_t)t->dims[d]*inner;
                memcpy(dst+(b*(uint32_t)o->dims[d]+channel)*inner*size,(uint8_t *)swin_tensor_data(g,n->in[k])+b*count*size,count*size);channel+=(uint32_t)t->dims[d];}}
        break; }
    case SLICE: {
        int64_t starts[8]={0},steps[8]={1,1,1,1,1,1,1,1};
        const SwinTensor *st=g->t+n->in[1];const void *sp=swin_tensor_data(g,n->in[1]);
        for(uint32_t k=0;k<st->elements;++k){int d=n->inputs>3?(int)integer(swin_tensor_data(g,n->in[3]),g->t[n->in[3]].dtype,k):(int)k;
            d=axis(d,a->rank);int64_t step=n->inputs>4?integer(swin_tensor_data(g,n->in[4]),g->t[n->in[4]].dtype,k):1;
            int64_t start=integer(sp,st->dtype,k),dim=a->dims[d];if(!step)return -24;
            if(start<0)start+=dim;
            if(step>0){if(start<0)start=0;if(start>dim)start=dim;}else {if(start<0)start=-1;if(start>=dim)start=dim-1;}
            starts[d]=start;steps[d]=step;}
        for(uint32_t i=0;i<o->elements;++i){uint32_t flat=i,offset=0,stride=1;
            for(int d=(int)a->rank-1;d>=0;--d){int64_t coord=starts[d]+(int64_t)(flat%(uint32_t)o->dims[d])*steps[d];flat/=(uint32_t)o->dims[d];
                if(coord<0||coord>=a->dims[d])return -25;offset+=(uint32_t)coord*stride;stride*=(uint32_t)a->dims[d];}
            memcpy(dst+i*size,(const uint8_t *)ap+offset*size,size);}break; }
    default:return -26;
    }
    return 0;
}
int swin_graph_run(SwinGraph *g) {
    for(uint32_t i=0;i<g->h->nodes;++i){int result=swin_graph_node(g,i);if(result)return result;
        ++g->executed_nodes;if(g->observe)g->observe(g,i);}
    return g->executed_jobs==g->h->jobs ? 0 : -27;
}
