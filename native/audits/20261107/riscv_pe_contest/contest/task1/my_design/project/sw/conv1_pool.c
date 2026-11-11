#include <stdint.h>

#include "conv1_pool_data.h"
#include "cnn_postprocess.h"

#if C1B_REQUANT_SHIFT != 24
#error "cnn_postprocess.h requires Q24 reference quantization"
#endif

/* Real Conv1 block: convolution, CPU ReLU/Q24 quantization and 2x2 pooling.
 * Four spatial positions, sixteen output channels.
 * Array row = one dot-product term; column = output channel;
 * byte/lane = spatial position. Keep inputs static while the array settles.
 * The header supplies sign-magnitude operand bytes and independent answers.
 */
#define PE_BASE 0x90000000u
#define PE_ROWS 16u
#define PE_COLS 16u
#define PE_LANES 4u

/* Ordinary RAM for independent observation through the ELF symbol table.
 * Current layout: [0..63] first partials, [64..127] second partials,
 * [128..191] combined results including each channel's bias exactly once.
 * [192..255] ReLU/Q24 values, [256..271] max-pooled channel values,
 * [272..295] quantization boundary cases, [296..303] pooling cases.
 * Within a 64-value group, index = column * 4 + lane.
 */
enum {
    RAW_OFFSET = C1T_BATCHES * C1T_OUTPUTS,
    REQUANT_OFFSET = (C1T_BATCHES + 1) * C1T_OUTPUTS,
    POOL_OFFSET = REQUANT_OFFSET + C1T_OUTPUTS,
    QUANT_CASE_OFFSET = POOL_OFFSET + PE_COLS,
    POOL_CASE_OFFSET = QUANT_CASE_OFFSET + C1B_QUANT_CASES
};
volatile int32_t conv1_pool_results[C1B_RESULT_WORDS];

static inline void wr(unsigned offset, uint32_t value) {
    *(volatile uint32_t *)(PE_BASE + offset) = value;
    __asm__ volatile ("fence iorw, iorw" ::: "memory");
}

static inline uint32_t rd(unsigned offset) {
    uint32_t value = *(volatile uint32_t *)(PE_BASE + offset);
    __asm__ volatile ("fence iorw, iorw" ::: "memory");
    return value;
}

static uint32_t pack_four(const uint8_t bytes[4]) {
    /* Widen before shifting: the highest byte may set bit 31, which must
     * be an unsigned operation rather than overflowing a signed int.
     */
    return (uint32_t)bytes[0]
         | ((uint32_t)bytes[1] << 8)
         | ((uint32_t)bytes[2] << 16)
         | ((uint32_t)bytes[3] << 24);
}

static void settle(void) {
    /* Conservative static-input wait, as in the existing single-point test.
     * This instruction loop does not imply exactly 128 CPU clock cycles.
     */
    for (volatile unsigned i = 0; i < 128; ++i)
        __asm__ volatile ("nop");
}

static void run_batch(const uint8_t input_hw[][4],
                      const uint8_t weight_hw[][16],
                      unsigned count, unsigned first) {
    /* Each row needs sixteen independent channel weights. Write all four
     * buffer words before pulsing that row's WE. SRAM address remains zero.
     * A short final batch explicitly writes zero weights into unused rows.
     */
    for (unsigned row = 0; row < PE_ROWS; ++row) {
        unsigned term = first + row;
        for (unsigned word = 0; word < PE_COLS / 4; ++word) {
            uint32_t packed = term < count
                ? pack_four(&weight_hw[term][4u * word]) : 0;
#if defined(C1B_INJECT_WEIGHT_ERROR) && C1B_INJECT_WEIGHT_ERROR
            /* Negative control: change only batch 0 / row 0 / column 0.
             * The schedule, golden values and all other operands stay intact.
             */
            if (term == 0 && word == 0)
                packed ^= 1u;
#endif
            wr(0x020 + 4u * word, packed);
        }
        wr(0x010, 1u << row);  /* Wrapper clears WE after one clock. */
    }

    /* One word packs the same K term for four different spatial positions.
     * The four multipliers within a PE share that row/column's one weight.
     */
    for (unsigned row = 0; row < PE_ROWS; ++row) {
        unsigned term = first + row;
        uint32_t packed = term < count ? pack_four(input_hw[term]) : 0;
        wr(0x100 + 4u * row, packed);
    }
    settle();
}

int main(void) {
    wr(0x000, 0);       /* All weight memories use SRAM address 0. */
    wr(0x008, 0xffff);  /* Enable all sixteen rows. */
    for (unsigned word = 0; word < C1T_OUTPUTS; ++word)
        wr(0x200 + 4u * word, 0);  /* All columns/lanes start from zero. */

    unsigned batch = 0;
    for (unsigned first = 0; first < C1T_TERMS; first += PE_ROWS, ++batch) {
        run_batch(C1T_INPUT_HW, C1T_WEIGHT_HW, C1T_TERMS, first);
        for (unsigned index = 0; index < C1T_OUTPUTS; ++index) {
            int32_t partial = (int32_t)rd(0x400 + 4u * index);
            conv1_pool_results[batch * C1T_OUTPUTS + index] = partial;
            if (partial != C1T_PARTIAL_GOLDEN[batch][index])
                return 1 + batch * C1T_OUTPUTS + index;
        }
    }

    for (unsigned index = 0; index < C1T_OUTPUTS; ++index) {
        int64_t total = (int64_t)C1T_BIAS[index / PE_LANES];
        /* Volatile reloads force actual CPU summation of the observed PE
         * values, even after the preceding checks establish their values.
         */
        for (unsigned part = 0; part < C1T_BATCHES; ++part)
            total += (int64_t)conv1_pool_results[part * C1T_OUTPUTS + index];
        conv1_pool_results[C1T_BATCHES * C1T_OUTPUTS + index] = (int32_t)total;
        if (total != (int64_t)C1T_ACC_GOLDEN[index])
            return 1 + C1T_BATCHES * C1T_OUTPUTS + index;
    }
    /* Read actual volatile raw results, then use the same postprocessing
     * routine for real data and the independent boundary cases below.
     */
    for (unsigned index = 0; index < C1T_OUTPUTS; ++index) {
        int32_t raw = conv1_pool_results[RAW_OFFSET + index];
        uint32_t quantized = relu_requant_q24(raw, C1B_REQUANT_MULT);
#if defined(C1B_INJECT_REQUANT_ERROR) && C1B_INJECT_REQUANT_ERROR
        if (index == 0)
            quantized ^= 1u;
#endif
        conv1_pool_results[REQUANT_OFFSET + index] = (int32_t)quantized;
        if (quantized != C1B_REQUANT_GOLDEN[index])
            return 1 + REQUANT_OFFSET + index;
    }

    /* Each channel's four lanes are the top-left, top-right, bottom-left
     * and bottom-right locations of one true 2x2 pooling window.
     */
    for (unsigned column = 0; column < PE_COLS; ++column) {
        uint32_t window[4];
        for (unsigned lane = 0; lane < PE_LANES; ++lane)
            window[lane] = (uint32_t)conv1_pool_results[
                REQUANT_OFFSET + column * PE_LANES + lane];
        uint32_t pooled = maxpool_four(window);
#if defined(C1B_INJECT_POOL_ERROR) && C1B_INJECT_POOL_ERROR
        if (column == 0)
            pooled ^= 1u;
#endif
        conv1_pool_results[POOL_OFFSET + column] = (int32_t)pooled;
        if (pooled != C1B_POOL_GOLDEN[column])
            return 1 + POOL_OFFSET + column;
    }

    for (unsigned index = 0; index < C1B_QUANT_CASES; ++index) {
        /* Force the same arithmetic to execute on the CPU for fixtures;
         * compile-time reference arrays must not turn it into a table copy.
         */
        volatile int32_t fixture_input = C1B_QUANT_CASE_INPUT[index];
        uint32_t quantized = relu_requant_q24(fixture_input, C1B_REQUANT_MULT);
        conv1_pool_results[QUANT_CASE_OFFSET + index] = (int32_t)quantized;
        if (quantized != C1B_QUANT_CASE_GOLDEN[index])
            return 1 + QUANT_CASE_OFFSET + index;
    }
    for (unsigned index = 0; index < C1B_POOL_CASES; ++index) {
        volatile uint32_t fixture_input[4];
        uint32_t window[4];
        for (unsigned lane = 0; lane < PE_LANES; ++lane)
            fixture_input[lane] = C1B_POOL_CASE_INPUT[index][lane];
        for (unsigned lane = 0; lane < PE_LANES; ++lane)
            window[lane] = fixture_input[lane];
        uint32_t pooled = maxpool_four(window);
        conv1_pool_results[POOL_CASE_OFFSET + index] = (int32_t)pooled;
        if (pooled != C1B_POOL_CASE_GOLDEN[index])
            return 1 + POOL_CASE_OFFSET + index;
    }
    return 0;
}
