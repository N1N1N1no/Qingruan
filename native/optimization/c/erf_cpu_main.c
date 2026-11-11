#include <stdint.h>
#include <math.h>
#include <string.h>
#include "accel_io.h"
#include "erf_cpu_vectors.h"
volatile uint32_t erf_cpu_actual[ERF_CPU_CASES],erf_cpu_tested,erf_cpu_failure;
volatile uint64_t erf_cpu_cycles;
int main(void){
 uint64_t begin=accel_cycle64();
 for(uint32_t i=0;i<ERF_CPU_CASES;++i){
  float x,y;uint32_t actual,raw=erf_cpu_inputs[i];memcpy(&x,&raw,4);y=erff(x);memcpy(&actual,&y,4);
  erf_cpu_actual[i]=actual;erf_cpu_tested=i+1;
  uint32_t want=erf_cpu_expected[i];
  int nan=(want&0x7fffffffu)>0x7f800000u;
  if(nan?(actual&0x7fffffffu)<=0x7f800000u:actual!=want){erf_cpu_failure=i+1;__asm__ volatile("fence iorw,iorw":::"memory");return 0x61;}
 }
 erf_cpu_cycles=accel_cycle64()-begin;
 __asm__ volatile("fence iorw,iorw":::"memory");return 0;
}
