#include <stdint.h>
#include <math.h>
/* Exactly the existing C graph's rounding order, without FMA contraction. */
void make_gelu_tables(uint8_t *out,uint32_t channels,const float *bias,float in_scale,
                     int32_t in_zero,float divisor,float addend,float multiplier,float out_scale,int32_t out_zero){
 for(uint32_t c=0;c<channels;++c)for(uint32_t raw=0;raw<256;++raw){
  int32_t q=(int8_t)raw;
  float dq=(float)(q-in_zero)*in_scale;
  float x=bias[c]+dq;
  float t=x/divisor;t=erff(t);t=t+addend;t=x*t;t=t*multiplier;
  float quant=nearbyintf(t/out_scale)+(float)out_zero;
  if(quant<-128)quant=-128;if(quant>127)quant=127;
  out[c*256+raw]=(uint8_t)(int8_t)quant;
 }
}
