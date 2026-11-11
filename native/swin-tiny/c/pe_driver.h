#ifndef SWIN_PE_DRIVER_H
#define SWIN_PE_DRIVER_H
#include <stdint.h>
#include <stddef.h>

typedef struct {
    void *ctx;
    void (*write32)(void *, uint32_t, uint32_t);
    uint32_t (*read32)(void *, uint32_t);
    void (*settle)(void *);
} SwinPeBus;

typedef struct {
    uint32_t batches, m, n, k;
    void *a, *b, *output;
    int16_t (*read_a)(void *, uint32_t, uint32_t, uint32_t);
    int16_t (*read_b)(void *, uint32_t, uint32_t, uint32_t);
    int32_t (*load_output)(void *, uint32_t, uint32_t, uint32_t);
    void (*store_output)(void *, uint32_t, uint32_t, uint32_t, int32_t);
} SwinPeJob;

typedef struct {
    uint64_t weight_loads, pe_passes, mmio_writes, mmio_reads;
} SwinPeStats;

int swin_pe_gemm(SwinPeBus *, const SwinPeJob *, SwinPeStats *);
uint8_t swin_pe_encode(int16_t);
int16_t swin_pe_plane(int16_t, unsigned);
#ifdef __riscv
SwinPeBus swin_pe_mmio(void);
#endif
#endif
