#include <stdint.h>

#include "conv1_tile_data.h"

/* Real Conv1 tile: four spatial positions, sixteen output channels.
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
 * Within a group, index = column * 4 + lane.
 */
volatile int32_t conv1_tile_results[(C1T_BATCHES + 1) * C1T_OUTPUTS];

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
#if defined(C1T_INJECT_WEIGHT_ERROR) && C1T_INJECT_WEIGHT_ERROR
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
            conv1_tile_results[batch * C1T_OUTPUTS + index] = partial;
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
            total += (int64_t)conv1_tile_results[part * C1T_OUTPUTS + index];
        conv1_tile_results[C1T_BATCHES * C1T_OUTPUTS + index] = (int32_t)total;
        if (total != (int64_t)C1T_ACC_GOLDEN[index])
            return 1 + C1T_BATCHES * C1T_OUTPUTS + index;
    }
    return 0;
}
