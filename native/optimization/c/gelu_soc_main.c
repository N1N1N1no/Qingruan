#include <stdint.h>
#include <math.h>
#include "accel_io.h"
#include "gelu_sample.h"
static volatile uint8_t source[160] __attribute__((aligned(64)));
static volatile uint8_t destination[160] __attribute__((aligned(64)));
volatile uint64_t gelu_kernel_cycles[2][2];
volatile uint32_t gelu_engine_results[2][3];
volatile uint32_t gelu_failure;
static uint8_t reference[128];
static uint8_t original_gelu(uint8_t raw,unsigned channel){
 float x=(float)((int8_t)raw-GELU_INPUT_ZERO)*GELU_INPUT_SCALE;
 x=gelu_bias[channel]+x;
 float y=x/GELU_SQRT_TWO;y=erff(y);y=y+GELU_ONE;y=x*y;y=y*GELU_HALF;
 float q=nearbyintf(y/GELU_OUTPUT_SCALE)+GELU_OUTPUT_ZERO;
 if(q<-128.0f)q=-128.0f;if(q>127.0f)q=127.0f;
 return (uint8_t)(int8_t)(int32_t)q;
}
int main(void){
 for(unsigned i=0;i<160;++i)source[i]=(uint8_t)(i*37+(i>>3)+13);
 for(unsigned n=0;n<2;++n){
  unsigned channels=n?32:16,rows=n?2:3,count=channels*rows;
  unsigned a=n?3:1,b=n?5:7;
  uint64_t begin=accel_cycle64();
  for(unsigned i=0;i<count;++i)reference[i]=original_gelu(source[a+i],i%channels);
  __asm__ volatile("fence iorw,iorw":::"memory");gelu_kernel_cycles[n][0]=accel_cycle64()-begin;
  for(unsigned i=0;i<160;++i)destination[i]=0xa5;
  __asm__ volatile("fence iorw,iorw":::"memory");begin=accel_cycle64();AccelResult result={0};
  int status=accel_gelu((uint32_t)(uintptr_t)(source+a),(uint32_t)(uintptr_t)(destination+b),(uint32_t)(uintptr_t)gelu_table,channels,rows,1000000,&result);
  gelu_kernel_cycles[n][1]=accel_cycle64()-begin;
  gelu_engine_results[n][0]=result.engine_cycles;gelu_engine_results[n][1]=result.streamed_bytes;gelu_engine_results[n][2]=result.error;
  if(status){gelu_failure=(uint32_t)status;return 0x20+n;}
  if(result.streamed_bytes!=count)return 0x30+n;
  for(unsigned i=0;i<160;++i){uint8_t expected=i>=b&&i<b+count?reference[i-b]:0xa5;
   if(destination[i]!=expected){gelu_failure=i+1;__asm__ volatile("fence iorw,iorw":::"memory");return 0x40+n;}
  }
 }
 AccelResult result={0};if(accel_gelu(0x80000000,0x80000000,0x80010000,16,1,1000000,&result)!=-4)return 0x50;
 __asm__ volatile("fence iorw,iorw":::"memory");return 0;
}
