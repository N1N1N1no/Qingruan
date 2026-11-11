#ifndef SWIN_GRAPH_H
#define SWIN_GRAPH_H
#include <stdint.h>
#include "pe_driver.h"
typedef struct {
    uint32_t magic,version,tensors,nodes,jobs,input,output,tensor_offset,node_offset,job_offset;
    uint32_t constant_bytes,arena_bytes,constant_base,arena_base,reserved[2];
} SwinHeader;
typedef struct {
    uint32_t offset,elements,dtype,rank,storage,raw;
    int32_t dims[8];
} SwinTensor;
typedef struct {
    uint32_t op,executor,inputs,output;
    int32_t in[8],attr[16];
    float f[4];
    int32_t job;
} SwinNode;
typedef struct { int32_t batches,m,n,k,araw,ascale,azero,braw,bscale,bzero,baxis,trans_b; } SwinJob;
typedef struct SwinGraph SwinGraph;
struct SwinGraph {
    const SwinHeader *h;
    const SwinTensor *t;
    const SwinNode *n;
    const SwinJob *j;
    const uint8_t *constants;
    uint8_t *arena;
    SwinPeBus *bus;
    SwinPeStats stats;
    uint32_t executed_nodes,executed_jobs;
    void (*observe)(SwinGraph *,uint32_t);
};
int swin_graph_init(SwinGraph *,const void *,const void *,void *,SwinPeBus *);
int swin_graph_node(SwinGraph *,uint32_t);
int swin_graph_run(SwinGraph *);
void *swin_tensor_data(SwinGraph *,uint32_t);
#endif
