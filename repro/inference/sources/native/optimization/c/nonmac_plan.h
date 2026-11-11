#ifndef SWIN_NONMAC_PLAN_H
#define SWIN_NONMAC_PLAN_H
#include "graph.h"
/* Patch bits identify scalar words copied from model memory at dispatch time.
 * Scalar addresses retain the graph's current values; no constants are tuned. */
typedef struct {uint32_t opcode,args[16],patch_mask,patch_address[16];} AccelPlannedCommand;
typedef int (*AccelPlanEmit)(void *,const AccelPlannedCommand *);
int accel_plan_node(SwinGraph *,uint32_t,AccelPlanEmit,void *);
int accel_plan_post(SwinGraph *,uint32_t,uint32_t,uint32_t,uint32_t,AccelPlanEmit,void *);
#endif
