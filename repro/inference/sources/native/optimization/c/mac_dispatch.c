#include "mac_dispatch.h"
int accel_emit_mac(void *context,const AccelPlannedCommand *c){
 AccelMacDispatch *m=context;if(!m||!m->dispatch||!c)return -243;
 if(c->opcode!=3)return accel_emit_planned(m->dispatch,c);
 if(c->patch_mask||!m->k_rows)return -244;
 const uint32_t *a=c->args;AccelDispatch *d=m->dispatch;
 int status=accel_gemm(a[0],a[1],a[2],a[3],a[4],a[5],(uint8_t)a[6],(uint8_t)a[7],a[8],d->timeout,&d->last);++d->commands;d->engine_cycles+=d->last.engine_cycles;d->streamed_bytes+=d->last.streamed_bytes;
 if(status)return status;uint64_t expected=(uint64_t)a[3]*a[4]*4*((a[5]+(uint64_t)m->k_rows-1)/m->k_rows);
 return d->last.streamed_bytes==expected?0:-245;
}
