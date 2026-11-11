#define ACCEL_SOC_ENTRY legacy_engine_checks
#include "accel_soc_main.c"
#include <string.h>
static volatile uint8_t softmax_source[2048] __attribute__((aligned(64)));
static volatile uint8_t softmax_destination[2048] __attribute__((aligned(64)));
static uint8_t softmax_expected[2048];
volatile uint64_t softmax_kernel_cycles[4][2];
volatile uint32_t softmax_engine_results[4][3],softmax_failure;
static int softmax_case(unsigned which){
 unsigned n=which==2?7:which==3?1:49,rows=which==1?3:which==2?2:1;
 unsigned a=which==1?5:which==2?7:which==3?0:3,b=which==1?7:which==3?1:3;
 unsigned gap=which==1?13:which==2?11:0,ss=which==2?0:n*4+gap,ds=n*4+gap;
 float values[49],exponents[49];
 for(unsigned i=0;i<2048;++i){softmax_source[i]=0x5a;softmax_destination[i]=0xa5;softmax_expected[i]=0xa5;}
 for(unsigned r=0;r<(ss?rows:1);++r)for(unsigned i=0;i<n;++i){
  float v=((int)((i*17+r*13)%67)-33)*0.125f;uint8_t raw[4];memcpy(raw,&v,4);
  for(unsigned k=0;k<4;++k)softmax_source[a+r*ss+i*4+k]=raw[k];
 }
 uint64_t begin=accel_cycle64();
 for(unsigned r=0;r<rows;++r){
  float maximum=-INFINITY,sum=0;
  for(unsigned i=0;i<n;++i){uint8_t raw[4];for(unsigned k=0;k<4;++k)raw[k]=softmax_source[a+r*ss+i*4+k];memcpy(values+i,raw,4);maximum=fmaxf(maximum,values[i]);}
  for(unsigned i=0;i<n;++i){exponents[i]=expf(values[i]-maximum);sum+=exponents[i];}
  for(unsigned i=0;i<n;++i){float v=exponents[i]/sum;memcpy(softmax_expected+b+r*ds+i*4,&v,4);}
 }
 softmax_kernel_cycles[which][0]=accel_cycle64()-begin;
 AccelResult result={0};begin=accel_cycle64();
 int status=accel_softmax((uint32_t)(uintptr_t)(softmax_source+a),(uint32_t)(uintptr_t)(softmax_destination+b),n,rows,ss,ds,1000000,&result);
 softmax_kernel_cycles[which][1]=accel_cycle64()-begin;
 softmax_engine_results[which][0]=result.engine_cycles;softmax_engine_results[which][1]=result.streamed_bytes;softmax_engine_results[which][2]=result.error;
 if(status){softmax_failure=(uint32_t)status;return 0xc0;}
 if(result.streamed_bytes!=n*rows*4){softmax_failure=result.streamed_bytes;return 0xc1;}
 for(unsigned i=0;i<2048;++i)if(softmax_destination[i]!=softmax_expected[i]){softmax_failure=i+1;return 0xc4;}
 return 0;
}
#ifndef SOFTMAX_SOC_ENTRY
#define SOFTMAX_SOC_ENTRY main
#endif
int SOFTMAX_SOC_ENTRY(void){
 int status=legacy_engine_checks();if(status)return status;
 for(unsigned i=0;i<4;++i){
  status=softmax_case(i);if(status)return status;
  status=matrix_case(0);if(status)return status;
 }
 __asm__ volatile("fence iorw,iorw":::"memory");return 0;
}
