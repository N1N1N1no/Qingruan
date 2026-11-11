#include <stdint.h>

#include "conv1_point_data.h"

/* A real Conv1 dot product, using column 0 / lane 0 of the existing array.
 * The generated header contains 27 input/weight bytes and independent golden
 * results. PE multiplicands use sign-magnitude; use *_HW, not int8_t bytes.
 * No streaming schedule is assumed: inputs remain static until readback.
 */
#define PE_BASE 0x90000000u
#define PE_ROWS 16u
#define CONV1_TERMS 27u

/* Ordinary RAM, visible to the testbench through the ELF symbol table.
 * [0] = first 16 products, [1] = remaining 11, [2] = both + bias once.
 */
volatile int32_t conv1_point_results[3];

static inline void wr(unsigned offset, uint32_t value) {
    *(volatile uint32_t *)(PE_BASE + offset) = value;
    __asm__ volatile ("fence iorw, iorw" ::: "memory");
}

static inline uint32_t rd(unsigned offset) {
    uint32_t value = *(volatile uint32_t *)(PE_BASE + offset);
    __asm__ volatile ("fence iorw, iorw" ::: "memory");
    return value;
}

static void settle(void) {
    /* Same conservative static-input wait as the original smoke test.
     * This is a loop of instructions, not an assumption of 128 CPU clocks.
     */
    for (volatile unsigned i = 0; i < 128; ++i)
        __asm__ volatile ("nop");
}

static int32_t run_batch(unsigned first_term) {
    /* All rows address SRAM slot 0. WE is a one-clock pulse, automatically
     * cleared by the wrapper. Select one row per write so every row can hold
     * a different weight; all columns except column 0 receive zero weights.
     */
    for (unsigned row = 0; row < PE_ROWS; ++row) {
        unsigned term = first_term + row;
        uint32_t weight = term < CONV1_TERMS ? C1P_WEIGHT_HW[term] : 0;
#if defined(C1P_INJECT_WEIGHT_ERROR) && C1P_INJECT_WEIGHT_ERROR
        /* Deliberate negative control: preserve the reference and schedule,
         * but corrupt one real PE operand. Never enabled in the normal run.
         */
        if (term == 0)
            weight ^= 1u;
#endif
        wr(0x020, weight);
        wr(0x010, 1u << row);
    }

    /* Only byte 0 (lane 0) carries input. In batch 2, rows 11..15 are
     * explicitly padded with zero. Keep every input unchanged while waiting
     * for the SRAM read, product registers and downward sums to settle.
     */
    for (unsigned row = 0; row < PE_ROWS; ++row) {
        unsigned term = first_term + row;
        uint32_t input = term < CONV1_TERMS ? C1P_INPUT_HW[term] : 0;
        wr(0x100 + 4u * row, input);
    }
    settle();
    return (int32_t)rd(0x400);
}

int main(void) {
    wr(0x000, 0);       /* Weight SRAM address 0. */
    wr(0x008, 0xffff);  /* Enable all 16 rows for writes and reads. */

    /* Clear the complete 16-column weight buffer before single-row writes.
     * Writing only its first word later leaves columns 1..15 at zero.
     */
    for (unsigned word = 0; word < 4; ++word)
        wr(0x020 + 4u * word, 0);

    /* Each batch starts from zero, not from the previous partial result.
     * CPU combines the two partial sums after both have been checked.
     */
    for (unsigned word = 0; word < 64; ++word)
        wr(0x200 + 4u * word, 0);

    int32_t first = run_batch(0);
    conv1_point_results[0] = first;
    if (first != C1P_PARTIAL_GOLDEN[0])
        return 1;

    int32_t second = run_batch(16);
    conv1_point_results[1] = second;
    if (second != C1P_PARTIAL_GOLDEN[1])
        return 2;

    /* Reload the recorded volatile values so the compiler cannot replace
     * this calculation with constants after the preceding golden checks.
     * Widen before adding the bias to avoid signed 32-bit overflow.
     */
    int64_t total = (int64_t)conv1_point_results[0]
                  + (int64_t)conv1_point_results[1] + (int64_t)C1P_BIAS;
    conv1_point_results[2] = (int32_t)total;
    if (total != (int64_t)C1P_ACC_GOLDEN)
        return 3;

    return 0;
}
