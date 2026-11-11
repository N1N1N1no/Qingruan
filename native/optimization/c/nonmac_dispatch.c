#include "nonmac_dispatch.h"
#include <string.h>
int accel_emit_planned(void *context,const AccelPlannedCommand *command){
 AccelDispatch *d=context;if(!d||!command||(command->opcode!=1&&(command->opcode<5||command->opcode>8))||command->patch_mask&~0xe000u||(command->opcode!=7&&command->patch_mask))return -220;
 uint32_t args[16];memcpy(args,command->args,sizeof(args));
 for(unsigned i=13;i<16;++i)if(command->patch_mask&(1u<<i)){
  uint32_t address=command->patch_address[i];if(address<0x80000000u||(uint64_t)address+4>0x84000000ull)return -221;
  memcpy(args+i,(const void *)(uintptr_t)address,4);
 }
 uint64_t expected=0;
 if(command->opcode==1)expected=(uint64_t)args[2]*args[3];
 else if(command->opcode==5)expected=(uint64_t)args[2]*args[3]*4;
 else if(command->opcode==6||command->opcode==7)expected=(uint64_t)args[4]*args[5]*4;
 else{if(args[3]<1||args[3]>4)return -222;expected=args[2];for(unsigned i=0;i<args[3];++i){if(!args[4+i]||expected>0x4000000ull/args[4+i])return -222;expected*=args[4+i];}}
 if(!expected||expected>0x4000000ull)return -223;
 int status=accel_execute(command->opcode,args,d->timeout,&d->last);++d->commands;
 d->engine_cycles+=d->last.engine_cycles;d->streamed_bytes+=d->last.streamed_bytes;
 if(status)return status;return d->last.streamed_bytes==expected?0:-224;
}
