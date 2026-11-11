/* Functional hybrid-model checkpoints at fully drained graph-node boundaries.
 * Only the activation arena persists between nodes: constants/tables are
 * immutable, and every MAC/GELU/Q command initializes its private cache/state.
 * The testbench's periodic bus-stall phase is restored with its logical tick.
 * This does not checkpoint a running RV32 CPU and is not full-SoC timing. */
#include <errno.h>
#include <unistd.h>
extern void rtl_read_tick(unsigned long long *);
extern void rtl_restore_tick(unsigned long long);
typedef struct {
 uint64_t magic;
 uint32_t version,next_node,fusion_index,executed_nodes,executed_jobs,arena_bytes,segments;
 uint32_t calls[5];
 uint64_t cycles[5],bytes[5],tick,checksum;
 char identity[65];
} ModelCheckpoint;
static uint64_t checkpoint_hash(uint64_t hash,const void *data,size_t bytes){
 const uint8_t *p=data;for(size_t i=0;i<bytes;++i){hash^=p[i];hash*=UINT64_C(1099511628211);}return hash;
}
static uint64_t engine_total(void){uint64_t n=0;for(unsigned i=1;i<5;++i)n+=cycles_by_opcode[i];return n;}
static int load_checkpoint(const char *path,const char *identity,SwinGraph *g,size_t nf,uint32_t *next,size_t *fi,uint32_t *segments){
 FILE *f=fopen(path,"rb");if(!f)return errno==ENOENT?0:-1;
 ModelCheckpoint h;
 if(fread(&h,1,sizeof(h),f)!=sizeof(h)||h.magic!=UINT64_C(0x315450434e495753)||h.version!=1||h.identity[64]||strcmp(h.identity,identity)||
  h.arena_bytes!=g->h->arena_bytes||h.next_node>g->h->nodes||h.executed_nodes!=h.next_node||h.executed_jobs>g->h->jobs||h.fusion_index>nf||h.calls[2]!=h.fusion_index){fclose(f);return -1;}
 uint64_t expected=h.checksum;h.checksum=0;
 if(fread(g->arena,1,h.arena_bytes,f)!=h.arena_bytes||fgetc(f)!=EOF){fclose(f);return -1;}fclose(f);
 uint64_t hash=checkpoint_hash(UINT64_C(14695981039346656037),&h,sizeof(h));hash=checkpoint_hash(hash,g->arena,h.arena_bytes);
 if(hash!=expected)return -1;
 memcpy(calls,h.calls,sizeof(calls));memcpy(cycles_by_opcode,h.cycles,sizeof(cycles_by_opcode));memcpy(bytes_by_opcode,h.bytes,sizeof(bytes_by_opcode));
 g->executed_nodes=h.executed_nodes;g->executed_jobs=h.executed_jobs;*next=h.next_node;*fi=h.fusion_index;*segments=h.segments+1;
 rtl_restore_tick(h.tick);
 printf("RTL_MODEL_CHECKPOINT_RESUMED next_node=%u logical_tick=%llu\n",*next,(unsigned long long)h.tick);fflush(stdout);return 1;
}
static int save_checkpoint(const char *path,const char *identity,SwinGraph *g,uint32_t next,size_t fi,uint32_t segments){
 ModelCheckpoint h;memset(&h,0,sizeof(h));
 h.magic=UINT64_C(0x315450434e495753);h.version=1;h.next_node=next;h.fusion_index=(uint32_t)fi;
 h.executed_nodes=g->executed_nodes;h.executed_jobs=g->executed_jobs;h.arena_bytes=g->h->arena_bytes;h.segments=segments;
 memcpy(h.calls,calls,sizeof(calls));memcpy(h.cycles,cycles_by_opcode,sizeof(cycles_by_opcode));memcpy(h.bytes,bytes_by_opcode,sizeof(bytes_by_opcode));
 unsigned long long tick;rtl_read_tick(&tick);h.tick=tick;memcpy(h.identity,identity,65);
 uint64_t hash=checkpoint_hash(UINT64_C(14695981039346656037),&h,sizeof(h));h.checksum=checkpoint_hash(hash,g->arena,h.arena_bytes);
 char temp[4096],info[4096];if(snprintf(temp,sizeof(temp),"%s.tmp",path)>=(int)sizeof(temp))return -1;
 FILE *f=fopen(temp,"wb");if(!f)return -1;
 int failed=fwrite(&h,1,sizeof(h),f)!=sizeof(h)||fwrite(g->arena,1,h.arena_bytes,f)!=h.arena_bytes||fflush(f)||fsync(fileno(f));
 if(fclose(f))failed=1;if(failed||rename(temp,path))return -1;
 if(snprintf(info,sizeof(info),"%s.json",path)>=(int)sizeof(info))return -1;
 f=fopen(info,"w");if(!f)return -1;
 fprintf(f,"{\"status\":\"CHECKPOINT_ONLY_NOT_INFERENCE_PASS\",\"header_bytes\":%zu,\"arena_bytes\":%u,\"next_node\":%u,\"fusion_index\":%u,\"executed_jobs\":%u,\"segments\":%u,\"logical_tick\":%llu,\"engine_cycles\":%llu,\"MAC_cycles\":%llu,\"GELU_cycles\":%llu,\"quantizer_cycles\":%llu,\"identity\":\"%s\",\"checksum\":\"%016llx\"}\n",sizeof(h),h.arena_bytes,h.next_node,h.fusion_index,h.executed_jobs,segments,(unsigned long long)h.tick,(unsigned long long)engine_total(),(unsigned long long)cycles_by_opcode[3],(unsigned long long)cycles_by_opcode[2],(unsigned long long)cycles_by_opcode[4],identity,(unsigned long long)h.checksum);
 fclose(f);printf("RTL_MODEL_CHECKPOINT_SAVED next_node=%u logical_tick=%llu\n",next,(unsigned long long)h.tick);fflush(stdout);return 0;
}
