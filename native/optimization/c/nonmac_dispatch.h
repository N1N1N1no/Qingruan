#ifndef SWIN_NONMAC_DISPATCH_H
#define SWIN_NONMAC_DISPATCH_H
#include "nonmac_plan.h"
#include "accel_io.h"
typedef struct{uint64_t timeout,engine_cycles,streamed_bytes;uint32_t commands;AccelResult last;}AccelDispatch;
int accel_emit_planned(void *,const AccelPlannedCommand *);
#endif
