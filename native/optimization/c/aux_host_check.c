/* Command composition test against unchanged graph.c, not an RTL simulator. */
#include "aux_plan.h"
#include <fenv.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define RAM_BASE 0x80000000u
#define RAM_BYTES 0x04000000u
#define WORKSPACE 0x83000000u
typedef struct{uint8_t *memory;uint64_t output_begin,output_end,work_begin,work_end;uint32_t commands;uint64_t bytes;int inject;}Context;
static void *load(const char *path){FILE *f=fopen(path,"rb");if(!f)return 0;fseek(f,0,SEEK_END);long size=ftell(f);rewind(f);void *p=malloc((size_t)size);if(!p||fread(p,1,(size_t)size,f)!=(size_t)size){free(p);fclose(f);return 0;}fclose(f);return p;}
static uint8_t *pointer(Context *c,uint64_t a,uint64_t bytes){if(a<RAM_BASE||a+bytes>(uint64_t)RAM_BASE+RAM_BYTES){fprintf(stderr,"AUX_HOST_RANGE_ERROR\n");exit(8);}return c->memory+a-RAM_BASE;}
static int write_allowed(Context *c,uint64_t a,uint64_t bytes){return(a>=c->output_begin&&a+bytes<=c->output_end)||(a>=c->work_begin&&a+bytes<=c->work_end);}
static void copy(Context *c,uint64_t source,uint64_t destination,uint32_t bytes){if(!write_allowed(c,destination,bytes)){fprintf(stderr,"AUX_HOST_WRITE_GUARD\n");exit(9);}memcpy(pointer(c,destination,bytes),pointer(c,source,bytes),bytes);c->bytes+=bytes;}
static int emit(void *context,const AccelPlannedCommand *command){
 Context *c=context;AccelPlannedCommand adapted=*command;if(c->inject&&!c->commands)adapted.args[0]+=4;const uint32_t *a=adapted.args;if(command->patch_mask)return -1;++c->commands;
 if(command->opcode==1){for(uint32_t r=0;r<a[3];++r)copy(c,a[0]+(uint64_t)r*a[4],a[1]+(uint64_t)r*a[5],a[2]);return 0;}
 if(command->opcode==8){
  uint64_t rows=1;for(uint32_t j=0;j<a[3];++j)rows*=a[4+j];
  for(uint64_t r=0;r<rows;++r){uint64_t source=a[0],q=r;for(uint32_t j=a[3];j>0;--j){source+=(q%a[3+j])*a[7+j];q/=a[3+j];}copy(c,source,a[1]+r*a[2],a[2]);}return 0;
 }
 if(command->opcode!=7||a[5]!=1||(a[10]!=0&&a[10]!=2))return -2;
 for(uint32_t i=0;i<a[4];++i){float x,y;memcpy(&x,pointer(c,a[0]+i*4ull,4),4);if(a[11]&1)memcpy(&y,a+13,4);else memcpy(&y,pointer(c,a[1]+i*4ull,4),4);
  float result=a[10]==0?x+y:x/y;uint64_t destination=a[3]+i*4ull;if(!write_allowed(c,destination,4))return -3;memcpy(pointer(c,destination,4),&result,4);c->bytes+=4;
 }return 0;
}
static uint32_t sample(unsigned channel,unsigned at){uint32_t v=(channel*2654435761u+at*2246822519u)^0x61294e3bu;
 if(channel%101==100)return 0x80000000u;if(channel%11==0&&at<2)return at?0xdf123456u:0x5f123456u;
 if((channel+at)%29==0)return at%2?0x80000000u:0;if((channel+at)%31==0)return v&0x807fffffu;return(v&0x807fffffu)|((112+v%17)<<23);
}
int main(int argc,char **argv){
 if(argc!=4&&argc!=5)return 2;fesetenv(FE_DFL_ENV);SwinHeader *h=load(argv[1]);uint8_t *constants=load(argv[2]);if(!h||!constants)return 3;
 uint8_t *actual=calloc(RAM_BYTES,1),*reference=calloc(RAM_BYTES,1);if(!actual||!reference)return 4;memcpy(actual+h->constant_base-RAM_BASE,constants,h->constant_bytes);
 SwinGraph planned,oracle;if(swin_graph_init(&planned,h,actual+h->constant_base-RAM_BASE,actual+h->arena_base-RAM_BASE,0)||swin_graph_init(&oracle,h,reference+h->constant_base-RAM_BASE,reference+h->arena_base-RAM_BASE,0))return 5;
 unsigned nodes=0;uint64_t commands=0,bytes=0,output_bytes=0;
 for(uint32_t i=0;i<h->nodes;++i){const SwinNode *n=planned.n+i;const SwinTensor *out=planned.t+n->output;
  if(n->executor!=1||out->dtype!=1||(n->op!=6&&n->op!=8&&n->op!=15))continue;
  memset(actual+h->arena_base-RAM_BASE,0xa5,h->arena_bytes);
  for(uint32_t p=0;p<n->inputs;++p){const SwinTensor *t=planned.t+n->in[p];if(t->dtype!=1||t->storage!=1)continue;uint8_t *data=swin_tensor_data(&planned,(uint32_t)n->in[p]);
   for(uint32_t k=0;k<t->elements;++k){uint32_t v=sample(k/49,k%49);memcpy(data+k*4,&v,4);}
  }
  memcpy(reference,actual,RAM_BYTES);if(swin_graph_node(&oracle,i))return 6;
  Context context={.memory=actual,.output_begin=(uint64_t)h->arena_base+out->offset,.output_end=(uint64_t)h->arena_base+out->offset+out->elements*4ull,.inject=argc==5};
  if(n->op==15){uint64_t row=out->elements*4ull,p1=(WORKSPACE+row+63)&~63ull,tr=(p1+row+63)&~63ull;context.work_begin=WORKSPACE;context.work_end=tr+planned.t[n->in[0]].elements*4ull;}
  int status=accel_plan_aux(&planned,i,WORKSPACE,emit,&context);if(status){fprintf(stderr,"AUX_HOST_PLAN_ERROR node=%u status=%d\n",i,status);return 10;}
  uint64_t before=context.work_begin?context.work_begin-RAM_BASE:RAM_BYTES,after=context.work_end?context.work_end-RAM_BASE:RAM_BYTES;
  if(memcmp(actual,reference,(size_t)before)||memcmp(actual+after,reference+after,RAM_BYTES-(size_t)after)){fprintf(stderr,"AUX_HOST_NUMERICAL_MISMATCH node=%u\n",i);return 7;}
  ++nodes;commands+=context.commands;bytes+=context.bytes;output_bytes+=out->elements*4ull;
 }
 if(nodes!=82||commands!=161||output_bytes!=28001280ull)return 11;FILE *report=fopen(argv[3],"w");if(!report)return 12;
 fprintf(report,"{\"status\":\"PASS\",\"nodes\":%u,\"commands\":%llu,\"output_bytes\":%llu,\"written_bytes\":%llu}\n",nodes,(unsigned long long)commands,(unsigned long long)output_bytes,(unsigned long long)bytes);fclose(report);
 puts("AUX_HOST_COMPOSITION_PASS original_graph_c_reference=1 all_output_bits_and_write_guards=1");free(actual);free(reference);free(h);free(constants);return 0;
}
