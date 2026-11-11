#include "pe_driver.h"
#include <limits.h>

/* No saturation: affine INT8 values up to +/-255 are represented exactly. */
int16_t swin_pe_plane(int16_t value, unsigned plane) {
    int x=(int)value+64;
    int high=x/128;
    if (x<0 && x%128) --high;
    return (int16_t)(plane ? high : (int)value-128*high);
}
uint8_t swin_pe_encode(int16_t value) {
    return (uint8_t)(value<0 ? (0x80u | (unsigned)(-value)) : (unsigned)value);
}
static void wr(SwinPeBus *bus,SwinPeStats *s,uint32_t address,uint32_t value) {
    bus->write32(bus->ctx,address,value);++s->mmio_writes;
}
static int32_t rd(SwinPeBus *bus,SwinPeStats *s,uint32_t address) {
    ++s->mmio_reads;return (int32_t)bus->read32(bus->ctx,address);
}

int swin_pe_gemm(SwinPeBus *bus,const SwinPeJob *job,SwinPeStats *stats) {
    if (!bus || !job || !stats || !job->batches || !job->m || !job->n || !job->k ||
        !job->read_a || !job->read_b || !job->load_output || !job->store_output) return -1;
    if (job->k > (uint32_t)(INT32_MAX/(255*255))) return -2;
    wr(bus,stats,0x008,0xffff);
    for (unsigned i=0;i<64;++i) wr(bus,stats,0x200+4*i,0);
    for (uint32_t batch=0;batch<job->batches;++batch) {
        for (uint32_t n0=0;n0<job->n;n0+=16) {
            /* Two planes x eight K blocks use all sixteen SRAM addresses. */
            for (uint32_t kg=0;kg<job->k;kg+=128) {
                uint32_t blocks=(job->k-kg+15)/16;
                if (blocks>8) blocks=8;
                for (uint32_t block=0;block<blocks;++block) {
                    for (unsigned bp=0;bp<2;++bp) {
                        wr(bus,stats,0x000,2*block+bp);
                        for (unsigned row=0;row<16;++row) {
                            uint32_t k=kg+16*block+row;
                            for (unsigned word=0;word<4;++word) {
                                uint32_t packed=0;
                                for (unsigned byte=0;byte<4;++byte) {
                                    uint32_t n=n0+4*word+byte;
                                    int16_t v=(k<job->k && n<job->n) ? job->read_b(job->b,batch,k,n) : 0;
                                    if (v < -255 || v > 255) return -3;
                                    packed |= (uint32_t)swin_pe_encode(swin_pe_plane(v,bp)) << (8*byte);
                                }
                                wr(bus,stats,0x020+4*word,packed);
                            }
                            wr(bus,stats,0x010,1u<<row);
                        }
                        ++stats->weight_loads;
                    }
                }
                for (uint32_t m0=0;m0<job->m;m0+=4) {
                    int64_t sums[64];
                    for (unsigned col=0;col<16;++col) for (unsigned lane=0;lane<4;++lane)
                        sums[col*4+lane]=(kg && m0+lane<job->m && n0+col<job->n)
                            ? job->load_output(job->output,batch,m0+lane,n0+col) : 0;
                    for (uint32_t block=0;block<blocks;++block) {
                        for (unsigned ap=0;ap<2;++ap) {
                            for (unsigned row=0;row<16;++row) {
                                uint32_t packed=0,k=kg+16*block+row;
                                for (unsigned lane=0;lane<4;++lane) {
                                    uint32_t m=m0+lane;
                                    int16_t v=(k<job->k && m<job->m) ? job->read_a(job->a,batch,m,k) : 0;
                                    if (v < -255 || v > 255) return -4;
                                    packed|=(uint32_t)swin_pe_encode(swin_pe_plane(v,ap))<<(8*lane);
                                }
                                wr(bus,stats,0x100+4*row,packed);
                            }
                            for (unsigned bp=0;bp<2;++bp) {
                                wr(bus,stats,0x000,2*block+bp);
                                bus->settle(bus->ctx);
                                for (unsigned index=0;index<64;++index) {
                                    int32_t partial=rd(bus,stats,0x400+4*index);
                                    sums[index]+=(int64_t)partial*((int64_t)1 << (7*(ap+bp)));
                                }
                                ++stats->pe_passes;
                            }
                        }
                    }
                    for (unsigned col=0;col<16;++col) for (unsigned lane=0;lane<4;++lane) {
                        int64_t v=sums[col*4+lane];
                        if (v<INT32_MIN || v>INT32_MAX) return -5;
                        if (m0+lane<job->m && n0+col<job->n)
                            job->store_output(job->output,batch,m0+lane,n0+col,(int32_t)v);
                    }
                }
            }
        }
    }
    return 0;
}
#ifdef __riscv
static void mmio_write(void *ctx,uint32_t offset,uint32_t value) {
    (void)ctx;*(volatile uint32_t *)(uintptr_t)(0x90000000u+offset)=value;
    __asm__ volatile("fence iorw, iorw" ::: "memory");
}
static uint32_t mmio_read(void *ctx,uint32_t offset) {
    (void)ctx;uint32_t value=*(volatile uint32_t *)(uintptr_t)(0x90000000u+offset);
    __asm__ volatile("fence iorw, iorw" ::: "memory");return value;
}
static void mmio_settle(void *ctx) {
    (void)ctx;__asm__ volatile(".rept 64\n nop\n .endr" ::: "memory");
}
SwinPeBus swin_pe_mmio(void) {
    SwinPeBus bus={0,mmio_write,mmio_read,mmio_settle};return bus;
}
#endif
