#include <stdint.h>
#include <math.h>
#include <string.h>
#include "accel_io.h"
#include "exp_cpu_vectors.h"
volatile uint32_t exp_cpu_actual[EXP_CPU_CASES],exp_cpu_tested,exp_cpu_failure;
volatile uint64_t exp_cpu_cycles;
int main(void){
 uint64_t begin=accel_cycle64();
 for(uint32_t i=0;i<EXP_CPU_CASES;++i){
  float x,y;uint32_t actual,raw=exp_cpu_inputs[i];memcpy(&x,&raw,4);y=expf(x);memcpy(&actual,&y,4);
  exp_cpu_actual[i]=actual;exp_cpu_tested=i+1;
  uint32_t want=exp_cpu_expected[i];
  int nan=(want&0x7fffffffu)>0x7f800000u;
  if(nan?(actual&0x7fffffffu)<=0x7f800000u:actual!=want){exp_cpu_failure=i+1;__asm__ volatile("fence iorw,iorw":::"memory");return 0x61;}
 }
 exp_cpu_cycles=accel_cycle64()-begin;
 __asm__ volatile("fence iorw,iorw":::"memory");return 0;
}
