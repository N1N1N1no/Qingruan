#define main gelu_only_main
#include "gelu_soc_main.c"
#undef main
#include "quant_sample.h"
static volatile int8_t matrix_a[645] __attribute__((aligned(64)));
static volatile int8_t matrix_b[512] __attribute__((aligned(64)));
static volatile int32_t matrix_c[52] __attribute__((aligned(64)));
volatile uint64_t mac_kernel_cycles[3][2];
volatile uint32_t mac_engine_results[3][3];
volatile uint32_t mac_failure;
static volatile float quant_source[40] __attribute__((aligned(64)));
static volatile uint8_t quant_destination[48] __attribute__((aligned(64)));
volatile uint64_t quant_kernel_cycles[2][2];
volatile uint32_t quant_engine_results[2][3];
volatile uint32_t quant_failure;
static int quant_case(unsigned which){
 unsigned n=which?17:37,b=which?3:7;uint8_t expected[40];
 for(unsigned i=0;i<n;++i)quant_source[i]=((float)(int)i-18.0f)*QUANT_SCALE*0.5f;
 quant_source[0]=-INFINITY;quant_source[1]=INFINITY;
 for(unsigned i=0;i<48;++i)quant_destination[i]=0xa5;
 uint64_t begin=accel_cycle64();
 for(unsigned i=0;i<n;++i){float q=nearbyintf(quant_source[i]/QUANT_SCALE)+QUANT_ZERO;
  if(q<-128.0f)q=-128.0f;if(q>127.0f)q=127.0f;expected[i]=(uint8_t)(int8_t)(int32_t)q;
 }
 quant_kernel_cycles[which][0]=accel_cycle64()-begin;
 begin=accel_cycle64();AccelResult result={0};int status=accel_quant((uint32_t)(uintptr_t)quant_source,(uint32_t)(uintptr_t)(quant_destination+b),(uint32_t)(uintptr_t)quant_table,n,1000000,&result);
 quant_kernel_cycles[which][1]=accel_cycle64()-begin;quant_engine_results[which][0]=result.engine_cycles;quant_engine_results[which][1]=result.streamed_bytes;quant_engine_results[which][2]=result.error;
 if(status){quant_failure=(uint32_t)status;return 0xa0+which;}
 if(result.streamed_bytes!=n)return 0xa2+which;
 for(unsigned i=0;i<48;++i){uint8_t want=i>=b&&i<b+n?expected[i-b]:0xa5;
  if(quant_destination[i]!=want){quant_failure=i+1;return 0xa4+which;}
 }
 return 0;
}
static int matrix_case(unsigned which){
 unsigned m=which==1?1:5,n=which==1?33:3,k=which==1?9:129,trans=which==1;
 int az=which==1?17:-128,bz=which==1?-1:127;
 int32_t expected[48];
 for(unsigned i=0;i<m*k;++i)matrix_a[i]=(int8_t)(i*37+13);
 for(unsigned i=0;i<n*k;++i)matrix_b[i]=(int8_t)(i*17+(i>>3)+7);
 for(unsigned i=0;i<52;++i)matrix_c[i]=0x5a1234a5;
 uint64_t begin=accel_cycle64();
 for(unsigned row=0;row<m;++row)for(unsigned col=0;col<n;++col){
  int32_t sum=0;
  for(unsigned inner=0;inner<k;++inner)sum+=((int)matrix_a[row*k+inner]-az)*((int)matrix_b[trans?col*k+inner:inner*n+col]-bz);
  expected[row*n+col]=sum;
 }
 mac_kernel_cycles[which][0]=accel_cycle64()-begin;
 begin=accel_cycle64();AccelResult result={0};
 int status=accel_gemm((uint32_t)(uintptr_t)matrix_a,(uint32_t)(uintptr_t)matrix_b,(uint32_t)(uintptr_t)(matrix_c+1),m,n,k,(uint8_t)az,(uint8_t)bz,trans,1000000,&result);
 mac_kernel_cycles[which][1]=accel_cycle64()-begin;
 mac_engine_results[which][0]=result.engine_cycles;mac_engine_results[which][1]=result.streamed_bytes;mac_engine_results[which][2]=result.error;
 if(status){mac_failure=(uint32_t)status;return 0x60+which;}
 if(result.streamed_bytes!=m*n*4*((k+127)/128))return 0x70+which;
 for(unsigned i=0;i<52;++i){int32_t want=i>=1&&i<=m*n?expected[i-1]:0x5a1234a5;
  if(matrix_c[i]!=want){mac_failure=i+1;return 0x80+which;}
 }
 return 0;
}
#ifndef ACCEL_SOC_ENTRY
#define ACCEL_SOC_ENTRY main
#endif
int ACCEL_SOC_ENTRY(void){
 int status=matrix_case(0);if(status)return status;
 status=matrix_case(1);if(status)return status;
 status=quant_case(0);if(status)return status;
 status=gelu_only_main();if(status)return status;
 status=quant_case(1);if(status)return status;
 status=matrix_case(2);if(status)return status;
 __asm__ volatile("fence iorw,iorw":::"memory");return 0;
}
