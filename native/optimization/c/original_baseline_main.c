/* Timed, unchanged unfused graph and original signed-magnitude PE driver.
 * Profiling/validation wrapper only. No new accelerator MMIO is called.
 */
#include "graph.h"
#include "baseline_layout.h"
#include <math.h>
#include <string.h>

volatile uint64_t inference_cycles, node_cycles[MODEL_NODES];
volatile uint64_t baseline_weight_loads, baseline_pe_passes;
volatile uint64_t baseline_mmio_writes, baseline_mmio_reads;
volatile uint32_t model_error_node, model_mac_jobs, model_top1;
volatile uint32_t model_expected_top1, model_max_error_bits;

static uint64_t baseline_cycle64(void) {
    uint32_t high, low, again;
    do {
        __asm__ volatile("csrr %0,mcycleh" : "=r"(high));
        __asm__ volatile("csrr %0,mcycle" : "=r"(low));
        __asm__ volatile("csrr %0,mcycleh" : "=r"(again));
    } while (high != again);
    return ((uint64_t)high << 32) | low;
}

int main(void) {
    const SwinHeader *h = (const SwinHeader *)(uintptr_t)MODEL_DESCRIPTOR_BASE;
    SwinPeBus bus = swin_pe_mmio();
    SwinGraph g;
    if (h->nodes != MODEL_NODES || h->jobs != 77 || h->reserved[0]) return 0x90;
    if (swin_graph_init(&g, h, (void *)(uintptr_t)h->constant_base,
                        (void *)(uintptr_t)h->arena_base, &bus)) return 0x91;
    __asm__ volatile("fence iorw,iorw" ::: "memory");
    uint64_t begin = baseline_cycle64();
    for (uint32_t i = 0; i < h->nodes; ++i) {
        uint64_t node_begin = baseline_cycle64();
        int status = swin_graph_node(&g, i);
        ++g.executed_nodes;
        node_cycles[i] = baseline_cycle64() - node_begin;
        if (status) { model_error_node = i + 1; return 0x93; }
    }
    __asm__ volatile("fence iorw,iorw" ::: "memory");
    inference_cycles = baseline_cycle64() - begin;
    model_mac_jobs = g.executed_jobs;
    baseline_weight_loads = g.stats.weight_loads;
    baseline_pe_passes = g.stats.pe_passes;
    baseline_mmio_writes = g.stats.mmio_writes;
    baseline_mmio_reads = g.stats.mmio_reads;
    if (g.executed_jobs != 77 || g.executed_nodes != MODEL_NODES ||
        baseline_weight_loads != BASELINE_WEIGHT_LOADS ||
        baseline_pe_passes != BASELINE_PE_PASSES ||
        baseline_mmio_writes != BASELINE_MMIO_WRITES ||
        baseline_mmio_reads != BASELINE_MMIO_READS) return 0x94;
    /* Same internal frozen-C check as optimized firmware; this is not an
     * organizer-specified numeric tolerance or official accuracy acceptance. */
    const float *out = swin_tensor_data(&g, h->output);
    const float *expected = (const float *)(uintptr_t)MODEL_EXPECTED_BASE;
    uint32_t count = g.t[h->output].elements, actual_top = 0, expected_top = 0;
    float peak = 0, max_error = 0;
    if (count != 1000) return 0x95;
    for (uint32_t i = 0; i < count; ++i) {
        if (!isfinite(out[i]) || !isfinite(expected[i])) return 0x95;
        float error = fabsf(out[i] - expected[i]), magnitude = fabsf(expected[i]);
        if (error > max_error) max_error = error;
        if (magnitude > peak) peak = magnitude;
        if (out[i] > out[actual_top]) actual_top = i;
        if (expected[i] > expected[expected_top]) expected_top = i;
    }
    model_top1 = actual_top; model_expected_top1 = expected_top;
    memcpy((void *)&model_max_error_bits, &max_error, 4);
    if (actual_top != expected_top || max_error > 2e-6f * fmaxf(1.0f, peak)) return 0x96;
    __asm__ volatile("fence iorw,iorw" ::: "memory");
    return 0;
}
