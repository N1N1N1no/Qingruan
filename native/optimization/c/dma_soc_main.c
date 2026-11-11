#include <stdint.h>
#include <string.h>
#include "accel_io.h"
static volatile uint8_t source[8192] __attribute__((aligned(4096)));
static volatile uint8_t destination[8192] __attribute__((aligned(4096)));
volatile uint64_t dma_kernel_cycles[4][2];
volatile uint32_t dma_engine_results[4][3];
volatile uint32_t dma_failure;
typedef struct{uint32_t a,b,length,rows,as,bs;}Case;
static const Case cases[4]={{3,7,33,5,47,61},{4089,4093,129,3,141,153},{1,2,1027,2,1050,1063},{5,11,17,7,0,31}};
static int check(const Case *c){
 for(uint32_t i=0;i<8192;++i){
  uint8_t expected=0xa5;
  for(uint32_t r=0;r<c->rows;++r)if(i>=c->b+r*c->bs&&i<c->b+r*c->bs+c->length)expected=source[c->a+r*c->as+i-c->b-r*c->bs];
  if(destination[i]!=expected){dma_failure=i+1;__asm__ volatile("fence iorw,iorw":::"memory");return -1;}
 }
 return 0;
}
int main(void){
 for(uint32_t i=0;i<8192;++i)source[i]=(uint8_t)(i*37+(i>>7)+13);
 for(unsigned n=0;n<4;++n){const Case *c=cases+n;
  for(uint32_t i=0;i<8192;++i)destination[i]=0xa5;
  __asm__ volatile("fence iorw,iorw":::"memory");uint64_t begin=accel_cycle64();
  for(uint32_t r=0;r<c->rows;++r)memcpy((void *)(destination+c->b+r*c->bs),(const void *)(source+c->a+r*c->as),c->length);
  __asm__ volatile("fence iorw,iorw":::"memory");dma_kernel_cycles[n][0]=accel_cycle64()-begin;
  if(check(c))return 0x10+n;
  for(uint32_t i=0;i<8192;++i)destination[i]=0xa5;
  __asm__ volatile("fence iorw,iorw":::"memory");begin=accel_cycle64();AccelResult result={0};
  int status=accel_copy((uint32_t)(uintptr_t)(source+c->a),(uint32_t)(uintptr_t)(destination+c->b),c->length,c->rows,c->as,c->bs,1000000,&result);
  dma_kernel_cycles[n][1]=accel_cycle64()-begin;
  dma_engine_results[n][0]=result.engine_cycles;dma_engine_results[n][1]=result.streamed_bytes;dma_engine_results[n][2]=result.error;
  if(status){dma_failure=(uint32_t)status;__asm__ volatile("fence iorw,iorw":::"memory");return 0x20+n;}
  if(result.streamed_bytes!=c->length*c->rows)return 0x30+n;
  if(check(c))return 0x40+n;
 }
 AccelResult result={0};if(accel_copy(0x7fffffff,0x80010000,8,1,8,8,1000000,&result)!=-4)return 0x50;
 __asm__ volatile("fence iorw,iorw":::"memory");return 0;
}
