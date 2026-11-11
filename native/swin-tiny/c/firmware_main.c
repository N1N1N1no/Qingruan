#include "graph.h"
/* Loader must place graph/constants/input at addresses in graph_manifest.
 * Existing SoC routes these addresses to external AXI RAM (64 MiB required).
 * This is the full-model firmware entry point, not a claim of completed RTL inference.
 */
volatile uint32_t swin_progress_node,swin_progress_jobs,swin_error;
volatile uint32_t swin_top1;
volatile float swin_top1_logit;
static void progress(SwinGraph *g,uint32_t index) {swin_progress_node=index;swin_progress_jobs=g->executed_jobs;}
int main(void) {
    const SwinHeader *h=(const SwinHeader *)0x80040000u;SwinPeBus bus=swin_pe_mmio();SwinGraph graph;
    int status=swin_graph_init(&graph,h,(const void *)(uintptr_t)h->constant_base,(void *)(uintptr_t)h->arena_base,&bus);
    if(status){swin_error=(uint32_t)status;return 1;}
    graph.observe=progress;status=swin_graph_run(&graph);swin_error=(uint32_t)status;
    if(status)return 2;
    const float *output=swin_tensor_data(&graph,h->output);uint32_t best=0;
    for(uint32_t i=1;i<graph.t[h->output].elements;++i)if(output[i]>output[best])best=i;
    swin_top1=best;swin_top1_logit=output[best];return 0;
}
