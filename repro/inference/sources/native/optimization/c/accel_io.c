#include "accel_io.h"
#include <stddef.h>
#ifndef ACCEL_MMIO_BASE
#define ACCEL_MMIO_BASE 0x90001000u
#endif
#define BASE ACCEL_MMIO_BASE
static uint32_t rd(uint32_t off){uint32_t v=*(volatile uint32_t *)(uintptr_t)(BASE+off);__asm__ volatile("fence iorw,iorw":::"memory");return v;}
static void wr(uint32_t off,uint32_t v){*(volatile uint32_t *)(uintptr_t)(BASE+off)=v;__asm__ volatile("fence iorw,iorw":::"memory");}
uint64_t accel_cycle64(void){uint32_t hi,lo,again;do{__asm__ volatile("csrr %0,mcycleh":"=r"(hi));__asm__ volatile("csrr %0,mcycle":"=r"(lo));__asm__ volatile("csrr %0,mcycleh":"=r"(again));}while(hi!=again);return ((uint64_t)hi<<32)|lo;}
int accel_execute(uint32_t opcode,const uint32_t arguments[16],uint64_t timeout,AccelResult *result){
 if(!arguments||!result||!timeout||rd(8)&1)return -1;
 for(unsigned i=0;i<16;++i)wr(0x100+8*i,arguments[i]);
 __asm__ volatile("fence iorw,iorw":::"memory");wr(0,opcode);
 uint64_t begin=accel_cycle64();
 while(!(rd(8)&2)){
  if(accel_cycle64()-begin>timeout){
   wr(0x28,1);uint64_t abort_begin=accel_cycle64();
   while(rd(8)&1)if(accel_cycle64()-abort_begin>timeout)return -3;
   result->error=rd(0x20);return -2;
  }
 }
 __asm__ volatile("fence iorw,iorw":::"memory");
 result->engine_cycles=rd(0x10);result->streamed_bytes=rd(0x18);result->error=rd(0x20);
 return result->error ? -(int)(0x100u|result->error):0;
}
int accel_copy(uint32_t source,uint32_t destination,uint32_t bytes,uint32_t rows,
               uint32_t source_stride,uint32_t destination_stride,uint64_t timeout,AccelResult *result){
 if(!bytes||!rows)return -4;
 uint64_t source_end=(uint64_t)source+(uint64_t)(rows-1)*source_stride+bytes;
 uint64_t destination_end=(uint64_t)destination+(uint64_t)(rows-1)*destination_stride+bytes;
 if(source<0x80000000u||destination<0x80000000u||source_end>0x84000000ull||destination_end>0x84000000ull)return -4;
 // All materialized graph outputs have a disjoint live allocation. Reject an
 // overlapping envelope rather than silently using copy semantics for a view.
 if((uint64_t)source<destination_end&&(uint64_t)destination<source_end)return -4;
 uint32_t arguments[16]={source,destination,bytes,rows,source_stride,destination_stride};
 return accel_execute(1,arguments,timeout,result);
}
int accel_gelu(uint32_t source,uint32_t destination,uint32_t table,uint32_t channels,uint32_t rows,uint64_t timeout,AccelResult *result){
 if(!channels||(channels&15)||!rows)return -4;
 uint64_t bytes=(uint64_t)channels*rows,table_bytes=(uint64_t)channels*256;
 uint64_t source_end=(uint64_t)source+bytes,destination_end=(uint64_t)destination+bytes,table_end=(uint64_t)table+table_bytes;
 if(source<0x80000000u||destination<0x80000000u||table<0x80000000u||source_end>0x84000000ull||destination_end>0x84000000ull||table_end>0x84000000ull)return -4;
 if(((uint64_t)source<destination_end&&(uint64_t)destination<source_end)||((uint64_t)table<destination_end&&(uint64_t)destination<table_end))return -4;
 uint32_t arguments[16]={source,destination,table,channels,rows};
 return accel_execute(2,arguments,timeout,result);
}
int accel_gemm(uint32_t a,uint32_t b,uint32_t c,uint32_t m,uint32_t n,uint32_t k,uint8_t az,uint8_t bz,uint32_t trans_b,uint64_t timeout,AccelResult *result){
 if(!m||!n||!k||m>65535||n>65535||k>32767||trans_b>1)return -4;
 uint64_t a_end=(uint64_t)a+(uint64_t)m*k,b_end=(uint64_t)b+(uint64_t)k*n,c_end=(uint64_t)c+(uint64_t)m*n*4;
 if(a<0x80000000u||b<0x80000000u||c<0x80000000u||a_end>0x84000000ull||b_end>0x84000000ull||c_end>0x84000000ull)return -4;
 if(((uint64_t)a<c_end&&(uint64_t)c<a_end)||((uint64_t)b<c_end&&(uint64_t)c<b_end))return -4;
 uint32_t arguments[16]={a,b,c,m,n,k,az,bz,trans_b};
 return accel_execute(3,arguments,timeout,result);
}
int accel_quant(uint32_t source,uint32_t destination,uint32_t table,uint32_t elements,uint64_t timeout,AccelResult *result){
 if(!elements)return -4;
 uint64_t source_end=(uint64_t)source+(uint64_t)elements*4,destination_end=(uint64_t)destination+elements,table_end=(uint64_t)table+1020;
 if(source<0x80000000u||destination<0x80000000u||table<0x80000000u||source_end>0x84000000ull||destination_end>0x84000000ull||table_end>0x84000000ull)return -4;
 if(((uint64_t)source<destination_end&&(uint64_t)destination<source_end)||((uint64_t)table<destination_end&&(uint64_t)destination<table_end))return -4;
 uint32_t arguments[16]={source,destination,table,elements};return accel_execute(4,arguments,timeout,result);
}
int accel_softmax(uint32_t source,uint32_t destination,uint32_t channels,uint32_t rows,uint32_t source_stride,uint32_t destination_stride,uint64_t timeout,AccelResult *result){
 // 512 floats also fit the smallest currently supported 128-bank candidate.
 if(!channels||channels>512||!rows||destination_stride<channels*4)return -4;
 uint64_t source_end=(uint64_t)source+(uint64_t)(rows-1)*source_stride+(uint64_t)channels*4;
 uint64_t destination_end=(uint64_t)destination+(uint64_t)(rows-1)*destination_stride+(uint64_t)channels*4;
 if(source<0x80000000u||destination<0x80000000u||source_end>0x84000000ull||destination_end>0x84000000ull)return -4;
 if((uint64_t)source<destination_end&&(uint64_t)destination<source_end)return -4;
 uint32_t arguments[16]={source,destination,channels,rows,source_stride,destination_stride};
 return accel_execute(5,arguments,timeout,result);
}
int accel_layernorm(uint32_t source,uint32_t destination,uint32_t scale,uint32_t bias,uint32_t elements,uint32_t rows,uint32_t source_stride,uint32_t destination_stride,uint32_t epsilon_bits,uint64_t timeout,AccelResult *result){
 if(!elements||elements>65535||!rows||destination_stride<elements*4)return -4;
 uint64_t source_end=(uint64_t)source+(uint64_t)(rows-1)*source_stride+(uint64_t)elements*4;
 uint64_t destination_end=(uint64_t)destination+(uint64_t)(rows-1)*destination_stride+(uint64_t)elements*4;
 uint64_t scale_end=(uint64_t)scale+(uint64_t)elements*4,bias_end=(uint64_t)bias+(uint64_t)elements*4;
 if(source<0x80000000u||destination<0x80000000u||scale<0x80000000u||bias<0x80000000u||source_end>0x84000000ull||destination_end>0x84000000ull||scale_end>0x84000000ull||bias_end>0x84000000ull)return -4;
 if(((uint64_t)source<destination_end&&(uint64_t)destination<source_end)||((uint64_t)scale<destination_end&&(uint64_t)destination<scale_end)||((uint64_t)bias<destination_end&&(uint64_t)destination<bias_end))return -4;
 uint32_t arguments[16]={source,destination,scale,bias,elements,rows,source_stride,destination_stride,epsilon_bits};
 return accel_execute(6,arguments,timeout,result);
}
static int vector_source_valid(uint32_t base,uint32_t rows,uint32_t stride,uint32_t bytes,uint32_t destination,uint64_t destination_end){
 uint64_t end=(uint64_t)base+(uint64_t)(rows-1)*stride+bytes;
 return base>=0x80000000u&&stride<=0x04000000u&&end<=0x84000000ull&&
  !((uint64_t)base<destination_end&&(uint64_t)destination<end);
}
int accel_vector(const AccelVector *v,uint64_t timeout,AccelResult *result){
 if(!v||v->operation>7||(v->flags&~3u)||!v->elements||v->elements>65535||!v->rows||v->rows>0x04000000u||
  v->destination_stride>0x04000000u||v->destination_stride<v->elements*4||(v->operation==5&&v->zero))return -4;
 uint64_t destination_end=(uint64_t)v->destination+(uint64_t)(v->rows-1)*v->destination_stride+(uint64_t)v->elements*4;
 uint32_t a_bytes=v->elements*((v->operation==3||v->operation==4)?1u:4u);
 if(v->destination<0x80000000u||destination_end>0x84000000ull||
  !vector_source_valid(v->a,v->rows,v->a_stride,a_bytes,v->destination,destination_end)||
  (!(v->flags&1)&&!vector_source_valid(v->b,v->rows,v->b_stride,v->elements*4,v->destination,destination_end))||
  (v->operation==7&&!(v->flags&2)&&!vector_source_valid(v->c,v->rows,v->c_stride,v->elements*4,v->destination,destination_end)))return -4;
 uint32_t arguments[16]={v->a,v->b,v->c,v->destination,v->elements,v->rows,v->a_stride,v->b_stride,v->c_stride,v->destination_stride,
  v->operation,v->flags,v->zero,v->scalar_b,v->scalar_c,v->scalar_d};
 return accel_execute(7,arguments,timeout,result);
}
int accel_reorder(uint32_t source,uint32_t destination,uint32_t row_bytes,uint32_t rank,const uint32_t dimensions[4],const uint32_t strides[4],uint64_t timeout,AccelResult *result){
 if(!dimensions||!strides||!rank||rank>4||!row_bytes||row_bytes>0x04000000u)return -4;
 uint64_t rows=1,extent=row_bytes;
 for(unsigned i=0;i<rank;++i){
  if(!dimensions[i]||dimensions[i]>0x04000000u||strides[i]>0x04000000u)return -4;
  rows*=dimensions[i];extent+=(uint64_t)(dimensions[i]-1)*strides[i];
  if(rows>0x04000000u||extent>0x04000000u||rows*row_bytes>0x04000000u)return -4;
 }
 uint64_t source_end=(uint64_t)source+extent,destination_end=(uint64_t)destination+rows*row_bytes;
 if(source<0x80000000u||destination<0x80000000u||source_end>0x84000000ull||destination_end>0x84000000ull||
  ((uint64_t)source<destination_end&&(uint64_t)destination<source_end))return -4;
 uint32_t arguments[16]={source,destination,row_bytes,rank,dimensions[0],dimensions[1],dimensions[2],dimensions[3],strides[0],strides[1],strides[2],strides[3]};
 return accel_execute(8,arguments,timeout,result);
}
