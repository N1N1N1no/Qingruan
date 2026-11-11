#include <stdint.h>

#include "conv2_full_data.h"
#include "cnn_postprocess.h"

#if C2F_REQUANT_SHIFT != 24
#error "cnn_postprocess.h requires the model's Q24 quantization"
#endif

#ifndef C2F_TILE_START
#define C2F_TILE_START 0
#endif
#ifndef C2F_TILE_COUNT
#define C2F_TILE_COUNT 3844
#endif

#define PE_BASE 0x90000000u
#define PE_ROWS 16u
#define PE_COLS 16u
#define PE_LANES 4u
#define INPUT_CHANNELS 32u
#define OUTPUT_CHANNELS 64u
#define TERMS (INPUT_CHANNELS * 3u * 3u)
#define BATCHES (TERMS / PE_ROWS)
#define POOL_SIDE 31u
#define POOL_PIXELS (POOL_SIDE * POOL_SIDE)
#define TOTAL_TILES ((OUTPUT_CHANNELS / PE_COLS) * POOL_PIXELS)
#define OUTPUT_BYTES (OUTPUT_CHANNELS * POOL_PIXELS)
#define BLOCK_TILES 32u
#define PARTIAL_WORDS (PE_COLS * PE_LANES)

_Static_assert(TERMS % PE_ROWS == 0, "Conv2 uses eighteen complete K batches");
_Static_assert(C2F_TILE_START >= 0 && C2F_TILE_COUNT > 0,
               "A run must contain a nonnegative, nonempty tile range");
_Static_assert(C2F_TILE_START + C2F_TILE_COUNT <= TOTAL_TILES,
               "Requested tile range exceeds Conv2");
_Static_assert(C2F_FAULT_TERM >= 0 && C2F_FAULT_TERM < TERMS,
               "The fault-injection term must lie within Conv2");
/* A sign-magnitude byte has magnitude at most 127. Even if every term has
 * the same sign, this bound covers every intermediate accumulator, not
 * just the final convolution result. The generator checks the bias bound.
 */
_Static_assert(C2F_MAX_ABS_BIAS <= INT32_MAX - TERMS * 127u * 127u,
               "The model must fit signed 32-bit prefix accumulators");

/* The complete actual output is CHW uint8[64][31][31]. Golden tensors are
 * exclusively in the test environment, never in this firmware.
 */
volatile uint8_t conv2_full_output[OUTPUT_BYTES];

/* [0] stage: 0=startup, 1=PE ready, 2=running, 3=complete;
 * [1] most recently completed global tile, UINT32_MAX before the first;
 * [2] completed tile count; [3] completed output byte count.
 */
volatile uint32_t conv2_full_progress[4];

/* Only 32 pool positions for 16 channels are live at once: 8192 bytes.
 * Every value originates in the model bias and actual MMIO PE results.
 */
static int32_t accumulators[BLOCK_TILES][PARTIAL_WORDS];

static inline void wr(unsigned offset, uint32_t value) {
    *(volatile uint32_t *)(PE_BASE + offset) = value;
    __asm__ volatile ("fence iorw, iorw" ::: "memory");
}

static inline int32_t rd(unsigned offset) {
    uint32_t value = *(volatile uint32_t *)(PE_BASE + offset);
    __asm__ volatile ("fence iorw, iorw" ::: "memory");
    return (int32_t)value;
}

static inline uint32_t pack_four(const uint8_t *bytes) {
    return (uint32_t)bytes[0]
         | ((uint32_t)bytes[1] << 8)
         | ((uint32_t)bytes[2] << 16)
         | ((uint32_t)bytes[3] << 24);
}

static inline void settle(void) {
    /* The PE has no software start/done register. Keep inputs stable for
     * the conservative propagation interval used by the earlier drivers.
     */
    __asm__ volatile (".rept 64\n\tnop\n\t.endr" ::: "memory");
}

static void load_weights(unsigned block_start, unsigned group, unsigned batch) {
    for (unsigned row = 0; row < PE_ROWS; ++row) {
        unsigned term = batch * PE_ROWS + row;
        for (unsigned word = 0; word < PE_COLS / 4u; ++word) {
            uint32_t packed = pack_four(
                &C2F_WEIGHT_HW[term][group * PE_COLS + word * 4u]);
#if defined(C2F_INJECT_WEIGHT_ERROR) && C2F_INJECT_WEIGHT_ERROR
            if (block_start == C2F_TILE_START && term == C2F_FAULT_TERM && word == 0)
                packed ^= 1u;
#else
            (void)block_start;
#endif
            wr(0x020 + 4u * word, packed);
        }
        wr(0x010, 1u << row);
    }
}

static void load_inputs(unsigned tile, unsigned batch) {
    unsigned position = tile % POOL_PIXELS;
    unsigned top = 2u * (position / POOL_SIDE);
    unsigned left = 2u * (position % POOL_SIDE);
    for (unsigned row = 0; row < PE_ROWS; ++row) {
        unsigned term = batch * PE_ROWS + row;
        unsigned channel = term / 9u;
        unsigned ky = (term % 9u) / 3u;
        unsigned kx = term % 3u;
        const uint8_t *upper = &C2F_INPUT_HW[channel][top + ky][left + kx];
        const uint8_t *lower = &C2F_INPUT_HW[channel][top + ky + 1u][left + kx];
        uint32_t packed = (uint32_t)upper[0]
                        | ((uint32_t)upper[1] << 8)
                        | ((uint32_t)lower[0] << 16)
                        | ((uint32_t)lower[1] << 24);
#if defined(C2F_INJECT_INPUT_ERROR) && C2F_INJECT_INPUT_ERROR
        if (tile == C2F_TILE_START && term == C2F_FAULT_TERM)
            packed ^= 1u;
#endif
        wr(0x100 + 4u * row, packed);
    }
    settle();
}

static void initialize_block(unsigned group, unsigned count) {
    for (unsigned local_tile = 0; local_tile < count; ++local_tile)
        for (unsigned column = 0; column < PE_COLS; ++column)
            for (unsigned lane = 0; lane < PE_LANES; ++lane)
                accumulators[local_tile][column * PE_LANES + lane]
                    = C2F_BIAS[group * PE_COLS + column];
}

static void finish_block(unsigned block_start, unsigned count) {
    unsigned group = block_start / POOL_PIXELS;
    for (unsigned local_tile = 0; local_tile < count; ++local_tile) {
        unsigned tile = block_start + local_tile;
        unsigned position = tile % POOL_PIXELS;
        for (unsigned column = 0; column < PE_COLS; ++column) {
            uint32_t quantized[PE_LANES];
            for (unsigned lane = 0; lane < PE_LANES; ++lane)
                quantized[lane] = relu_requant_q24(
                    accumulators[local_tile][column * PE_LANES + lane],
                    C2F_REQUANT_MULT);
            uint32_t pooled = maxpool_four(quantized);
#if defined(C2F_INJECT_POOL_ERROR) && C2F_INJECT_POOL_ERROR
            if (tile == C2F_TILE_START && column == 0)
                pooled ^= 1u;
#endif
            unsigned channel = group * PE_COLS + column;
            conv2_full_output[channel * POOL_PIXELS + position] = (uint8_t)pooled;
        }
        conv2_full_progress[1] = tile;
        conv2_full_progress[2] = tile - C2F_TILE_START + 1u;
        conv2_full_progress[3] = (tile - C2F_TILE_START + 1u) * PE_COLS;
    }
}

int main(void) {
    conv2_full_progress[1] = UINT32_MAX;
    wr(0x000, 0);
    wr(0x008, 0xffff);
    for (unsigned word = 0; word < PARTIAL_WORDS; ++word)
        wr(0x200 + 4u * word, 0);
    conv2_full_progress[0] = 1;

    unsigned end = C2F_TILE_START + C2F_TILE_COUNT;
    for (unsigned block_start = C2F_TILE_START; block_start < end;) {
        unsigned group = block_start / POOL_PIXELS;
        unsigned count = end - block_start;
        unsigned group_remaining = POOL_PIXELS - block_start % POOL_PIXELS;
        if (count > BLOCK_TILES)
            count = BLOCK_TILES;
        if (count > group_remaining)
            count = group_remaining;
        initialize_block(group, count);
        conv2_full_progress[0] = 2;

        /* One K slice is written once, then reused across up to 32 nearby
         * positions. Eighteen slices exceed the PE's sixteen SRAM slots;
         * this bounded block avoids reloading all weights for every tile.
         * Address zero remains selected throughout this firmware.
         */
        for (unsigned batch = 0; batch < BATCHES; ++batch) {
            load_weights(block_start, group, batch);
            for (unsigned local_tile = 0; local_tile < count; ++local_tile) {
                load_inputs(block_start + local_tile, batch);
                for (unsigned index = 0; index < PARTIAL_WORDS; ++index)
                    accumulators[local_tile][index] += rd(0x400 + 4u * index);
            }
        }
        finish_block(block_start, count);
        block_start += count;
    }
    conv2_full_progress[0] = 3;
    __asm__ volatile ("fence iorw, iorw" ::: "memory");
    return 0;
}
