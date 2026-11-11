#include "graph.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef struct{uint32_t start,end,input,output,channels,elements,offset;}Fusion;
static void *load(const char *p,size_t *size){FILE *f=fopen(p,"rb");if(!f){perror(p);exit(2);}fseek(f,0,SEEK_END);long n=ftell(f);rewind(f);
 if(n<0)exit(2);void *data=malloc((size_t)n);if(!data||fread(data,1,(size_t)n,f)!=(size_t)n)exit(2);fclose(f);*size=(size_t)n;return data;}
static unsigned width(uint32_t type){return type==1||type==6?4:type==7?8:1;}
static int fuse(SwinGraph *g,const Fusion *f,const uint8_t *tables,size_t bytes){
 if(f->end>=g->h->nodes||f->input>=g->h->tensors||f->output>=g->h->tensors||!f->channels||
    f->elements!=g->t[f->input].elements||f->elements!=g->t[f->output].elements||
    g->t[f->input].dtype!=3||g->t[f->output].dtype!=3||(uint64_t)f->offset+(uint64_t)f->channels*256>bytes)return -1;
 const uint8_t *a=swin_tensor_data(g,f->input);uint8_t *out=swin_tensor_data(g,f->output);const uint8_t *lut=tables+f->offset;
 // The original plan may reuse the input allocation during a fused span.
 // Process overlapping byte ranges in memmove order, without materializing FP32.
 if((uintptr_t)out>(uintptr_t)a&&(uintptr_t)out<(uintptr_t)a+f->elements){
  for(uint32_t i=f->elements;i;){--i;out[i]=lut[(i%f->channels)*256+a[i]];}
 }else for(uint32_t i=0;i<f->elements;++i)out[i]=lut[(i%f->channels)*256+a[i]];
 return 0;
}
int main(int argc,char **argv){
 if(argc!=5&&argc!=7)return 2;
 size_t size;void *image=load(argv[1],&size);SwinHeader *h=image;
 if((h->reserved[0]&~1u)||((h->reserved[0]&1u)&&argc!=7)){fprintf(stderr,"fusion manifest required\n");return 9;}
 void *constants=load(argv[2],&size),*arena=calloc(h->arena_bytes,1);
 SwinGraph g;if(swin_graph_init(&g,image,constants,arena,0))return 3;
 void *input=load(argv[3],&size);if(size!=(size_t)g.t[h->input].elements*width(g.t[h->input].dtype))return 4;
 memcpy(swin_tensor_data(&g,h->input),input,size);free(input);
 Fusion *fusions=0;uint8_t *tables=0;size_t fusion_bytes=0,table_bytes=0,fi=0;
 if(argc==7){tables=load(argv[5],&table_bytes);fusions=load(argv[6],&fusion_bytes);if(fusion_bytes%sizeof(Fusion))return 5;}
 uint32_t launched=0;
 for(uint32_t i=0;i<h->nodes;++i){int status;
  if(fi<fusion_bytes/sizeof(Fusion)&&fusions[fi].start==i){const Fusion *f=fusions+fi++;status=fuse(&g,f,tables,table_bytes);g.executed_nodes+=f->end-f->start+1;i=f->end;++launched;}
  else {status=swin_graph_node(&g,i);++g.executed_nodes;}
  if(status){fprintf(stderr,"node=%u error=%d\n",i,status);return 6;}
 }
 if(g.executed_jobs!=h->jobs||fi!=fusion_bytes/sizeof(Fusion))return 7;
 FILE *out=fopen(argv[4],"wb");if(!out)return 8;fwrite(swin_tensor_data(&g,h->output),width(g.t[h->output].dtype),g.t[h->output].elements,out);fclose(out);
 printf("OPT_HOST_PASS original_nodes=%u MAC_jobs=%u GELU_fusions=%u\n",g.executed_nodes,g.executed_jobs,launched);
 free(tables);free(fusions);free(arena);free(constants);free(image);return 0;
}
