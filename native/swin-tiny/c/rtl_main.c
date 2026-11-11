#include "pe_driver.h"
#include "rtl_vectors.h"
volatile int32_t swin_rtl_results[RESULT_COUNT];
typedef struct {const int16_t *a,*b;uint32_t m,n,k;volatile int32_t *out;} Matrix;
static int16_t ra(void *ctx,uint32_t b,uint32_t m,uint32_t k){Matrix *c=ctx;return c->a[(b*c->m+m)*c->k+k];}
static int16_t rb(void *ctx,uint32_t b,uint32_t k,uint32_t n){Matrix *c=ctx;int16_t v=c->b[(b*c->k+k)*c->n+n];
#ifdef SWIN_INJECT_ERROR
    if(c->a==A_0&&!b&&!k&&!n)++v;
#endif
    return v;}
static int32_t load(void *ctx,uint32_t b,uint32_t m,uint32_t n){Matrix *c=ctx;return c->out[(b*c->m+m)*c->n+n];}
static void store(void *ctx,uint32_t b,uint32_t m,uint32_t n,int32_t v){Matrix *c=ctx;c->out[(b*c->m+m)*c->n+n]=v;}
int main(void){SwinPeBus bus=swin_pe_mmio();SwinPeStats stats={0};
    for(uint32_t i=0;i<VECTOR_COUNT;++i){const Vector *v=VECTORS+i;Matrix c={v->a,v->b,v->m,v->n,v->k,swin_rtl_results+v->offset};
        SwinPeJob job={v->batch,v->m,v->n,v->k,&c,&c,&c,ra,rb,load,store};
        if(swin_pe_gemm(&bus,&job,&stats))return (int)i+1;}
    return 0;}
