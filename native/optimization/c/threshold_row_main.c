#include <stdint.h>
#include <math.h>
#include <string.h>
#include "accel_io.h"
#include "threshold_row_vectors.h"
volatile uint32_t threshold_actual[49],threshold_quant[49],threshold_failure;
volatile uint64_t threshold_cycles;
int main(void){
 float x[49],e[49],maximum=-INFINITY,sum=0;
 uint64_t begin=accel_cycle64();
 for(unsigned i=0;i<49;++i){uint32_t raw=threshold_input[i];memcpy(x+i,&raw,4);maximum=fmaxf(maximum,x[i]);}
 for(unsigned i=0;i<49;++i){e[i]=expf(x[i]-maximum);sum+=e[i];}
 for(unsigned i=0;i<49;++i){
  float v=e[i]/sum;uint32_t raw;memcpy(&raw,&v,4);threshold_actual[i]=raw;
  float q=nearbyintf(v/THRESHOLD_SCALE)+THRESHOLD_ZERO;
  if(q<-128.0f)q=-128.0f;if(q>127.0f)q=127.0f;
  threshold_quant[i]=(uint8_t)(int8_t)(int32_t)q;
 }
 threshold_cycles=accel_cycle64()-begin;
 for(unsigned i=0;i<49;++i)if(threshold_actual[i]!=threshold_expected[i]){threshold_failure=i+1;__asm__ volatile("fence iorw,iorw":::"memory");return 0xd1;}
 if(threshold_quant[0]!=134){threshold_failure=50;return 0xd2;}
 __asm__ volatile("fence iorw,iorw":::"memory");return 0;
}
