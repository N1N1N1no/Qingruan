#ifndef SWIN_AUX_PLAN_H
#define SWIN_AUX_PLAN_H
#include "nonmac_plan.h"
/* F32 Slice/Concat and sequential AvgPool over the existing DMA/FP engines.
 * Small integer shape metadata stays with the original CPU dispatcher. */
int accel_plan_aux(SwinGraph *,uint32_t,uint32_t,AccelPlanEmit,void *);
#endif
