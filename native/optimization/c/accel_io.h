#ifndef SWIN_ACCEL_IO_H
#define SWIN_ACCEL_IO_H
#include <stdint.h>
typedef struct{uint32_t engine_cycles,streamed_bytes,error;}AccelResult;
typedef struct{
 uint32_t a,b,c,destination,elements,rows,a_stride,b_stride,c_stride,destination_stride;
 uint32_t operation,flags,zero,scalar_b,scalar_c,scalar_d;
}AccelVector;
uint64_t accel_cycle64(void);
int accel_execute(uint32_t,const uint32_t[16],uint64_t,AccelResult *);
int accel_copy(uint32_t,uint32_t,uint32_t,uint32_t,uint32_t,uint32_t,uint64_t,AccelResult *);
int accel_gelu(uint32_t,uint32_t,uint32_t,uint32_t,uint32_t,uint64_t,AccelResult *);
int accel_gemm(uint32_t,uint32_t,uint32_t,uint32_t,uint32_t,uint32_t,uint8_t,uint8_t,uint32_t,uint64_t,AccelResult *);
int accel_quant(uint32_t,uint32_t,uint32_t,uint32_t,uint64_t,AccelResult *);
int accel_softmax(uint32_t,uint32_t,uint32_t,uint32_t,uint32_t,uint32_t,uint64_t,AccelResult *);
int accel_layernorm(uint32_t,uint32_t,uint32_t,uint32_t,uint32_t,uint32_t,uint32_t,uint32_t,uint32_t,uint64_t,AccelResult *);
int accel_vector(const AccelVector *,uint64_t,AccelResult *);
int accel_reorder(uint32_t,uint32_t,uint32_t,uint32_t,const uint32_t[4],const uint32_t[4],uint64_t,AccelResult *);
#endif
