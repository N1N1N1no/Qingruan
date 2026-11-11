#include "graph.h"
#include "profile_vectors.h"
#include <string.h>
#ifdef SWIN_PROFILE_HOST
#include <stdio.h>
#endif
volatile uint32_t profile_cycles[PROFILE_COUNT*4];
volatile uint32_t profile_values[PROFILE_WORDS];
static uint8_t arena[PROFILE_ARENA];
static uint32_t cycles(void){
#ifdef __riscv
 uint32_t v;__asm__ volatile("csrr %0, mcycle":"=r"(v));return v;
#else
 return 0;
#endif
}
#ifdef SWIN_PROFILE_HOST
int main(int argc,char **argv){(void)argc;
#else
int main(void){
#endif
 uint32_t offset=0;
 for(unsigned i=0;i<PROFILE_COUNT;++i){const Probe *p=PROBES+i;SwinGraph g;
  int status=swin_graph_init(&g,p->graph,p->constants,arena,0);if(status)return 1;
  uint32_t first=cycles();status=swin_graph_node(&g,0);uint32_t last=cycles();
  profile_cycles[4*i]=last-first;profile_cycles[4*i+1]=p->elements;profile_cycles[4*i+2]=(uint32_t)status;profile_cycles[4*i+3]=p->bytes;
  if(status)return 2;
  memcpy((void *)(profile_values+offset),swin_tensor_data(&g,g.h->output),p->bytes);offset+=(p->bytes+3)/4;
 }
#ifdef SWIN_PROFILE_HOST
 FILE *f=fopen(argv[1],"wb");if(!f)return 3;fwrite((const void *)profile_values,4,PROFILE_WORDS,f);fclose(f);
#endif
 return 0;
}
