#include "mac_plan.h"
#include <stdio.h>
#include <stdlib.h>
static unsigned node,batch;static FILE *out;
static int emit(void *ctx,const AccelPlannedCommand *c){
 (void)ctx;fprintf(out,"{\"node\":%u,\"batch\":%u,\"opcode\":%u,\"args\":[",node,batch,c->opcode);
 for(unsigned i=0;i<16;++i)fprintf(out,"%s%u",i?",":"",c->args[i]);fputs("]}\n",out);return ferror(out)?-1:0;
}
static void *load(const char *path){FILE *f=fopen(path,"rb");if(!f)return 0;fseek(f,0,SEEK_END);long size=ftell(f);rewind(f);void *p=malloc((size_t)size);if(!p||fread(p,1,(size_t)size,f)!=(size_t)size){fclose(f);free(p);return 0;}fclose(f);return p;}
void *swin_tensor_data(SwinGraph *g,uint32_t id){const SwinTensor *t=g->t+id;return t->storage==0?(void *)(g->constants+t->offset):0;}
int main(int argc,char **argv){
 if(argc!=4)return 2;SwinHeader *h=load(argv[1]);void *constants=load(argv[2]);if(!h||!constants)return 3;
 SwinGraph g={0};g.h=h;g.t=(void *)((uint8_t *)h+h->tensor_offset);g.n=(void *)((uint8_t *)h+h->node_offset);g.j=(void *)((uint8_t *)h+h->job_offset);g.constants=constants;
 out=fopen(argv[3],"w");if(!out)return 4;
 for(node=0;node<h->nodes;++node)if(g.n[node].executor==3){const SwinJob *j=g.j+g.n[node].job;
  for(batch=0;batch<(uint32_t)j->batches;++batch){uint32_t accumulator,temporary;int status=accel_plan_mac(&g,node,batch,0x83000000u,emit,0,&accumulator,&temporary);if(status){fprintf(stderr,"node=%u batch=%u status=%d\n",node,batch,status);return 5;}}
 }
 if(fclose(out))return 6;free(h);free(constants);puts("MAC_C_RAW_BINDING_DUMP_PASS");return 0;
}
