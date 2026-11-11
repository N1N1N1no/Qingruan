#define SOFTMAX_SOC_ENTRY softmax_prior_checks
#include "softmax_soc_main.c"
static volatile uint8_t ln_source[16384] __attribute__((aligned(64)));
static volatile uint8_t ln_destination[16384] __attribute__((aligned(64)));
static uint8_t ln_expected[16384];
static float ln_scale[1536] __attribute__((aligned(64))),ln_bias[1536] __attribute__((aligned(64)));
volatile uint64_t layernorm_kernel_cycles[3][2];
volatile uint32_t layernorm_engine_results[3][3],layernorm_failure;
static float ln_input(unsigned at){uint8_t bytes[4];for(unsigned k=0;k<4;++k)bytes[k]=ln_source[at+k];float x;memcpy(&x,bytes,4);return x;}
static int layernorm_case(unsigned which){
 unsigned n=which==0?96:which==1?1536:7,rows=which==2?2:1;
 unsigned a=which==1?3:5,b=which==1?7:5,gap=which?12:0;
 unsigned ss=which==2?0:n*4+gap,ds=n*4+gap;
 for(unsigned i=0;i<16384;++i){ln_source[i]=0x5a;ln_destination[i]=0xa5;ln_expected[i]=0xa5;}
 for(unsigned i=0;i<n;++i){
  float x=((int)((i*29)%113)-56)*0.0625f;uint8_t raw[4];memcpy(raw,&x,4);
  for(unsigned k=0;k<4;++k)ln_source[a+i*4+k]=raw[k];
  ln_scale[i]=((int)(i%17)-8)*0.125f;ln_bias[i]=((int)(i%13)-6)*0.03125f;
 }
 const float epsilon=1e-5f;uint32_t epsilon_bits;memcpy(&epsilon_bits,&epsilon,4);
 uint64_t begin=accel_cycle64();
 for(unsigned r=0;r<rows;++r){
  float sum=0,ssq=0;
  for(unsigned i=0;i<n;++i){float x=ln_input(a+r*ss+i*4);sum+=x;ssq+=x*x;}
  float mean=sum/(float)n,var=ssq/(float)n-mean*mean;
  float inverse=1.0f/sqrtf(fmaxf(var,0.0f)+epsilon);
  for(unsigned i=0;i<n;++i){float y=(ln_input(a+r*ss+i*4)-mean)*inverse*ln_scale[i]+ln_bias[i];memcpy(ln_expected+b+r*ds+i*4,&y,4);}
 }
 layernorm_kernel_cycles[which][0]=accel_cycle64()-begin;
 AccelResult result={0};begin=accel_cycle64();
 int status=accel_layernorm((uint32_t)(uintptr_t)(ln_source+a),(uint32_t)(uintptr_t)(ln_destination+b),(uint32_t)(uintptr_t)ln_scale,(uint32_t)(uintptr_t)ln_bias,n,rows,ss,ds,epsilon_bits,2000000,&result);
 layernorm_kernel_cycles[which][1]=accel_cycle64()-begin;
 layernorm_engine_results[which][0]=result.engine_cycles;layernorm_engine_results[which][1]=result.streamed_bytes;layernorm_engine_results[which][2]=result.error;
 if(status){layernorm_failure=(uint32_t)status;return 0xe0;}
 if(result.streamed_bytes!=n*rows*4){layernorm_failure=result.streamed_bytes;return 0xe1;}
 for(unsigned i=0;i<16384;++i)if(ln_destination[i]!=ln_expected[i]){layernorm_failure=i+1;return 0xe4;}
 return 0;
}
#ifndef LAYERNORM_SOC_ENTRY
#define LAYERNORM_SOC_ENTRY main
#endif
int LAYERNORM_SOC_ENTRY(void){
 int status=legacy_engine_checks();if(status)return status;
 status=softmax_case(0);if(status)return status;
 for(unsigned i=0;i<3;++i){status=layernorm_case(i);if(status)return status;status=matrix_case(0);if(status)return status;}
 status=softmax_case(1);if(status)return status;
 __asm__ volatile("fence iorw,iorw":::"memory");return 0;
}
