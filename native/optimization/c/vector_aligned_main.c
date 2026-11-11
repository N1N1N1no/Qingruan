/* Aligned arithmetic comparison: direct array loads/stores, no inner memcpy. */
#include "accel_io.h"
static volatile float input_a[288] __attribute__((aligned(64))),input_b[96] __attribute__((aligned(64))),input_c[96] __attribute__((aligned(64)));
static volatile int32_t input_i[288] __attribute__((aligned(64)));
static volatile uint8_t input_u[256] __attribute__((aligned(64)));
static volatile int8_t input_s[256] __attribute__((aligned(64)));
static volatile float scale_a=0.00125f,scale_b=0.03125f;
static float expected[288] __attribute__((aligned(64)));
static volatile union{float f[1056];uint32_t u[1056];}output __attribute__((aligned(64)));
volatile uint64_t vector_aligned_cycles[8][2];
volatile uint32_t vector_aligned_results[8][3],vector_aligned_failure;
static uint32_t bits(float f){union{float f;uint32_t u;}x={.f=f};return x.u;}
static int run(unsigned op){
 unsigned n=(op==3||op==4)?256:96,rows=(op==3||op==4)?1:3,count=n*rows;
 for(unsigned i=0;i<1056;++i)output.u[i]=0xa55ac33c;
 float sa=scale_a,sb=scale_b;uint64_t begin=accel_cycle64();
 switch(op){
  case 0:for(unsigned i=0;i<count;++i)expected[i]=input_a[i]+input_b[i%96];break;
  case 1:for(unsigned i=0;i<count;++i)expected[i]=input_a[i]*input_b[i%96];break;
  case 2:for(unsigned i=0;i<count;++i)expected[i]=input_a[i]/sb;break;
  case 3:for(unsigned i=0;i<count;++i)expected[i]=(float)((int)input_s[i]+37)*sb;break;
  case 4:for(unsigned i=0;i<count;++i)expected[i]=(float)((int)input_u[i]-173)*sb;break;
  case 5:for(unsigned i=0;i<count;++i)expected[i]=(float)input_i[i]*input_b[i%96];break;
  case 6:for(unsigned i=0;i<count;++i){float y=(float)input_i[i]*sa;y=y*input_b[i%96];expected[i]=y;}break;
  case 7:for(unsigned i=0;i<count;++i){float y=(float)input_i[i]*sa;y=y*input_b[i%96];expected[i]=y+input_c[i%96];}break;
 }
 vector_aligned_cycles[op][0]=accel_cycle64()-begin;
 AccelVector v={(uint32_t)(uintptr_t)input_a,(uint32_t)(uintptr_t)input_b,(uint32_t)(uintptr_t)input_c,(uint32_t)(uintptr_t)(output.f+16),n,rows,n*4,0,0,n*4,op,0,0,bits(sa),0,0};
 if(op>=5)v.a=(uint32_t)(uintptr_t)input_i;
 if(op==2){v.flags=1;v.scalar_b=bits(sb);}
 if(op==3||op==4){v.a=(uint32_t)(uintptr_t)(op==3?(const volatile void *)input_s:(const volatile void *)input_u);v.a_stride=n;v.flags=1;v.zero=op==3?(uint8_t)-37:173;v.scalar_b=bits(sb);}
 AccelResult r={0};begin=accel_cycle64();int status=accel_vector(&v,2000000,&r);vector_aligned_cycles[op][1]=accel_cycle64()-begin;
 vector_aligned_results[op][0]=r.engine_cycles;vector_aligned_results[op][1]=r.streamed_bytes;vector_aligned_results[op][2]=r.error;
 if(status||r.streamed_bytes!=count*4)return 0x240+op;
 for(unsigned i=0;i<1056;++i){uint32_t want=i>=16&&i<16+count?bits(expected[i-16]):0xa55ac33c;if(output.u[i]!=want){vector_aligned_failure=i+1;return 0x250+op;}}
 return 0;
}
int main(void){
 for(unsigned i=0;i<288;++i){input_a[i]=((int)(i%37)-18)*0.3125f;input_i[i]=(int32_t)(i*1234567)-18000000;}
 for(unsigned i=0;i<96;++i){input_b[i]=0.125f+(i%7)*0.0625f;input_c[i]=((int)(i%11)-5)*0.25f;}
 for(unsigned i=0;i<256;++i){input_u[i]=(uint8_t)i;input_s[i]=(int8_t)i;}
 for(unsigned i=0;i<8;++i){int status=run(i);if(status)return status;}
 __asm__ volatile("fence iorw,iorw":::"memory");return 0;
}
