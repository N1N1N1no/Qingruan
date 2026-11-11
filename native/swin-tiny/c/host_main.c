#include "graph.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
static const char *dump_dir;
static void *load(const char *path,size_t expected) {
    FILE *f=fopen(path,"rb");if(!f){perror(path);exit(2);}fseek(f,0,SEEK_END);long size=ftell(f);rewind(f);
    if(size<0||(expected&&(size_t)size!=expected)){fprintf(stderr,"Invalid file size %s\n",path);exit(2);}
    void *data=malloc((size_t)size);if(!data||fread(data,1,(size_t)size,f)!=(size_t)size)exit(2);fclose(f);return data;
}
static void observe(SwinGraph *g,uint32_t index) {
    const SwinNode *n=g->n+index;
    if(dump_dir&&n->executor!=0&&n->executor!=2){
        char path[4096];snprintf(path,sizeof(path),"%s/node_%04u.bin",dump_dir,index);FILE *f=fopen(path,"wb");
        const SwinTensor *t=g->t+n->output;size_t w=t->dtype==1||t->dtype==6?4:t->dtype==7?8:1;
        if(!f||fwrite(swin_tensor_data(g,n->output),w,t->elements,f)!=t->elements)exit(3);fclose(f);
    }
    if(n->executor==3){printf("HOST_JOB %u/%u node=%u\n",g->executed_jobs,g->h->jobs,index);fflush(stdout);}
}
static void teacher_load(SwinGraph *g,int32_t id,const char *dir) {
    if(id<0||g->t[id].storage!=1)return;
    const SwinTensor *t=g->t+id;size_t w=t->dtype==1||t->dtype==6?4:t->dtype==7?8:1;
    char path[4096];snprintf(path,sizeof(path),"%s/tensor_%04d.bin",dir,id);
    void *data=load(path,(size_t)t->elements*w);memcpy(swin_tensor_data(g,(uint32_t)id),data,(size_t)t->elements*w);free(data);
}
static int probe(SwinGraph *g,const char *dir,const char *outdir) {
    dump_dir=outdir;
    for(uint32_t i=0;i<g->h->nodes;++i){const SwinNode *n=g->n+i;
        if(n->executor==0||n->executor==2)continue;
        for(uint32_t k=0;k<n->inputs;++k)teacher_load(g,n->in[k],dir);
        if(n->executor==3){const SwinJob *j=g->j+n->job;teacher_load(g,j->araw,dir);teacher_load(g,j->braw,dir);}
        int status=swin_graph_node(g,i);if(status)return status;observe(g,i);
    }
    return 0;
}
int main(int argc,char **argv) {
    if(argc<5){fprintf(stderr,"host_run graph.bin constants.bin input.bin output.bin [dump_dir]\n");return 2;}
    void *image=load(argv[1],0);const SwinHeader *h=image;void *constants=load(argv[2],h->constant_bytes),*arena=calloc(h->arena_bytes,1);
    SwinGraph g;int status=swin_graph_init(&g,image,constants,arena,0);if(status)return 3;
    if(argc==6&&!strcmp(argv[3],"--probe")){
        status=probe(&g,argv[4],argv[5]);printf("HOST_PROBE status=%d jobs=%u\n",status,g.executed_jobs);
        free(arena);free(constants);free(image);return status?1:0;
    }
    size_t input_bytes=(size_t)g.t[h->input].elements*4;void *input=load(argv[3],input_bytes);memcpy(swin_tensor_data(&g,h->input),input,input_bytes);free(input);
    if(argc>5)dump_dir=argv[5];g.observe=observe;
    clock_t start=clock();status=swin_graph_run(&g);
    printf("HOST_GRAPH status=%d nodes=%u jobs=%u seconds=%.3f\n",status,g.executed_nodes,g.executed_jobs,(double)(clock()-start)/CLOCKS_PER_SEC);
    if(!status){FILE *f=fopen(argv[4],"wb");if(!f)return 3;fwrite(swin_tensor_data(&g,h->output),4,g.t[h->output].elements,f);fclose(f);}
    free(arena);free(constants);free(image);return status?1:0;
}
