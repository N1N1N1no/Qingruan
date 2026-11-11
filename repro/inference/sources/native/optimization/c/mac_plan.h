#ifndef SWIN_MAC_PLAN_H
#define SWIN_MAC_PLAN_H
#include "nonmac_plan.h"
/* Emits hardware im2col only for Conv, then GEMM over raw tensor addresses.
 * Returned scratch addresses are INT32 accumulators and separate FP output. */
int accel_plan_mac(SwinGraph *,uint32_t,uint32_t,uint32_t,AccelPlanEmit,void *,uint32_t *,uint32_t *);
#endif
