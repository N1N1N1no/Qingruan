#ifndef SWIN_MAC_DISPATCH_H
#define SWIN_MAC_DISPATCH_H
#include "nonmac_dispatch.h"
typedef struct{AccelDispatch *dispatch;uint32_t k_rows;}AccelMacDispatch;
int accel_emit_mac(void *,const AccelPlannedCommand *);
#endif
