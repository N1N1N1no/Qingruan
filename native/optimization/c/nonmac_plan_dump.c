#include "nonmac_plan.h"
#include <stdio.h>
#include <stdlib.h>
static unsigned node,batch,post;static FILE *out;
static int emit(void *ctx,const AccelPlannedCommand *c){
 (void)ctx;fprintf(out,"{\"node\":%u,\"batch\":%u,\"post\":%u,\"opcode\":%u,\"args\":[",node,batch,post,c->opcode);
 for(unsigned i=0;i<16;++i)fprintf(out,"%s%u",i?",":"",c->args[i]);fprintf(out,"],\"patch_mask\":%u,\"patch_address\":[",c->patch_mask);
 for(unsigned i=0;i<16;++i)fprintf(out,"%s%u",i?",":"",c->patch_address[i]);fputs("]}\n",out);return ferror(out)?-1:0;
}
static void *load(const char *path){FILE *f=fopen(path,"rb");if(!f)return 0;fseek(f,0,SEEK_END);long size=ftell(f);rewind(f);void *p=malloc((size_t)size);if(!p||fread(p,1,(size_t)size,f)!=(size_t)size){fclose(f);free(p);return 0;}fclose(f);return p;}
int main(int argc,char **argv){
 if(argc!=5)return 2;SwinHeader *h=load(argv[1]);void *constants=load(argv[2]);uint32_t *fusions=load(argv[4]);if(!h||!constants||!fusions)return 3;
 FILE *manifest=fopen(argv[4],"rb");if(!manifest)return 3;fseek(manifest,0,SEEK_END);long fusion_bytes=ftell(manifest);fclose(manifest);if(fusion_bytes<0||fusion_bytes%28)return 3;unsigned fusion_count=(unsigned)fusion_bytes/28,fi=0;
 SwinGraph g={0};g.h=h;g.t=(void *)((uint8_t *)h+h->tensor_offset);g.n=(void *)((uint8_t *)h+h->node_offset);g.j=(void *)((uint8_t *)h+h->job_offset);g.constants=constants;
 out=fopen(argv[3],"w");if(!out)return 4;
 for(node=0;node<h->nodes;++node){if(fi<fusion_count&&node==fusions[fi*7]){node=fusions[fi*7+1];++fi;continue;}batch=0;post=0;int status=accel_plan_node(&g,node,emit,0);if(status<0){fprintf(stderr,"node=%u status=%d\n",node,status);return 5;}
  if(g.n[node].executor==3){const SwinJob *j=g.j+g.n[node].job;
   uint64_t a=0x83000000ull,b=(a+(uint64_t)j->m*j->k+63)&~63ull,c=(b+(uint64_t)j->k*j->n+63)&~63ull,d=(c+(uint64_t)j->m*j->n*4+63)&~63ull;
   if(d+(uint64_t)j->m*j->n*4>0x83fff000ull)return 6;
   post=1;for(batch=0;batch<(uint32_t)j->batches;++batch){status=accel_plan_post(&g,node,batch,(uint32_t)c,(uint32_t)d,emit,0);if(status){fprintf(stderr,"post node=%u status=%d\n",node,status);return 7;}}
  }
 }
 if(fclose(out)||fi!=fusion_count)return 8;free(h);free(constants);free(fusions);puts("NONMAC_C_PLAN_DUMP_PASS");return 0;
}
void *swin_tensor_data(SwinGraph *g,uint32_t id){const SwinTensor *t=g->t+id;return t->storage==0?(void *)(g->constants+t->offset):0;}
