/* Inserted into an isolated copy of graph.c, whose unchanged addressing
 * callbacks define the original Conv/MatMul/Gemm semantics. */
extern uint8_t *rtl_memory;
extern int rtl_call(unsigned,const uint32_t[16],uint32_t *,uint32_t *);
static int rtl_mac(Mac *c){
 const SwinJob *j=c->job;SwinGraph *g=c->g;
 int az=(int)integer(swin_tensor_data(g,j->azero),g->t[j->azero].dtype,0);
 const SwinTensor *zt=g->t+j->bzero;
 int bz=(int)integer(swin_tensor_data(g,j->bzero),zt->dtype,0);
 for(uint32_t col=0;col<zt->elements;++col)
  if(integer(swin_tensor_data(g,j->bzero),zt->dtype,col)!=bz)return -91;
 uint64_t aa=0x83000000ull,bb=(aa+(uint64_t)j->m*j->k+63)&~63ull;
 uint64_t cc=(bb+(uint64_t)j->k*j->n+63)&~63ull,end=cc+(uint64_t)j->m*j->n*4;
 if(end>0x84000000ull)return -92;
 int8_t *a=(int8_t *)(rtl_memory+aa-0x80000000ull),*b=(int8_t *)(rtl_memory+bb-0x80000000ull);
 int32_t *out=(int32_t *)(rtl_memory+cc-0x80000000ull);
 for(uint32_t batch=0;batch<(uint32_t)j->batches;++batch){
  for(uint32_t row=0;row<(uint32_t)j->m;++row)for(uint32_t inner=0;inner<(uint32_t)j->k;++inner){
   int raw=read_a(c,batch,row,inner)+az;if(raw< -128||raw>127)return -93;a[row*j->k+inner]=(int8_t)raw;
  }
  for(uint32_t inner=0;inner<(uint32_t)j->k;++inner)for(uint32_t col=0;col<(uint32_t)j->n;++col){
   int raw=read_b(c,batch,inner,col)+bz;if(raw< -128||raw>127)return -94;
   b[j->trans_b?col*j->k+inner:inner*j->n+col]=(int8_t)raw;
  }
  uint32_t args[16]={(uint32_t)aa,(uint32_t)bb,(uint32_t)cc,(uint32_t)j->m,(uint32_t)j->n,(uint32_t)j->k,(uint8_t)az,(uint8_t)bz,(uint32_t)j->trans_b};
  uint32_t cycles=0,bytes=0;if(rtl_call(3,args,&cycles,&bytes))return -95;
  for(uint32_t row=0;row<(uint32_t)j->m;++row)for(uint32_t col=0;col<(uint32_t)j->n;++col)store_acc(c,batch,row,col,out[row*j->n+col]);
 }
 return 0;
}
