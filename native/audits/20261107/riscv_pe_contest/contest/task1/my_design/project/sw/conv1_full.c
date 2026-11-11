#include <stdint.h>

#include "conv1_full_data.h"
#include "cnn_postprocess.h"

#if C1F_REQUANT_SHIFT != 24
#error "cnn_postprocess.h requires the model's Q24 quantization"
#endif

#ifndef C1F_TILE_START
#define C1F_TILE_START 0
#endif
#ifndef C1F_TILE_COUNT
#define C1F_TILE_COUNT 8192
#endif

#define PE_BASE 0x90000000u
#define PE_ROWS 16u
#define PE_COLS 16u
#define PE_LANES 4u
#define CONV_TERMS 27u
#define POOL_SIDE 64u
#define POOL_PIXELS (POOL_SIDE * POOL_SIDE)
#define TOTAL_TILES (2u * POOL_PIXELS)
#define OUTPUT_BYTES (32u * POOL_PIXELS)

_Static_assert(C1F_TILE_START >= 0 && C1F_TILE_COUNT > 0,
               "A run must contain at least one nonnegative tile");
_Static_assert(C1F_TILE_START + C1F_TILE_COUNT <= TOTAL_TILES,
               "Requested tile range exceeds Conv1");

/* CHW uint8 output: channel 0's 64x64 pooled image, then channel 1, etc.
 * No full 32-bit convolution tensor or CPU golden tensor is allocated.
 */
volatile uint8_t conv1_full_output[OUTPUT_BYTES];

/* Ordinary RAM status, independently observable by the testbench.
 * [0] stage: 0=startup, 1=weights ready, 2=running, 3=complete;
 * [1] most recently completed global tile id (UINT32_MAX before any tile);
 * [2] number of completed tiles; [3] number of output bytes written.
 */
volatile uint32_t conv1_full_progress[4];

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
    /* 64 explicit instructions, with no RAM-dependent loop. One new input
     * needs at most 16 horizontal register hops, two product stages and 15
     * downward psum stages. This conservative wait exceeds that bound even
     * before the ordered MMIO accesses/read latency are counted.
     * Timing correctness remains checked against every actual PE read.
     */
    __asm__ volatile (".rept 64\n\tnop\n\t.endr" ::: "memory");
}

static void preload_weights(void) {
    wr(0x008, 0xffff);
    for (unsigned word = 0; word < PE_COLS * PE_LANES; ++word)
        wr(0x200 + 4u * word, 0);

    /* Four of the existing sixteen SRAM slots hold immutable weights:
     * slot = output-channel group * 2 + K chunk.
     * Every row/column is explicitly written, including zero-padded terms.
     */
    for (unsigned slot = 0; slot < 4; ++slot) {
        unsigned group = slot / 2;
        unsigned first = (slot % 2) * PE_ROWS;
        wr(0x000, slot);
        for (unsigned row = 0; row < PE_ROWS; ++row) {
            unsigned term = first + row;
            for (unsigned word = 0; word < PE_COLS / 4; ++word) {
                uint32_t packed = term < CONV_TERMS
                    ? pack_four(&C1F_WEIGHT_HW[term][group * PE_COLS + 4u * word])
                    : 0;
#if defined(C1F_INJECT_WEIGHT_ERROR) && C1F_INJECT_WEIGHT_ERROR
                if (slot == 0 && row == 0 && word == 0)
                    packed ^= 1u;
#endif
                wr(0x020 + 4u * word, packed);
            }
            wr(0x010, 1u << row);
        }
    }
}

static void prepare_window(unsigned pool_row, unsigned pool_col,
                           uint32_t words[32]) {
    unsigned top = 2u * pool_row;
    unsigned left = 2u * pool_col;
    unsigned term = 0;
    for (unsigned channel = 0; channel < 3; ++channel) {
        for (unsigned ky = 0; ky < 3; ++ky) {
            const uint8_t *upper = &C1F_INPUT_HW[channel][top + ky][left];
            const uint8_t *lower = &C1F_INPUT_HW[channel][top + ky + 1][left];
            for (unsigned kx = 0; kx < 3; ++kx) {
                /* lane order: top-left, top-right, bottom-left, bottom-right.
                 * Input activations are already encoded nonnegative bytes.
                 */
                words[term++] = (uint32_t)upper[kx]
                    | ((uint32_t)upper[kx + 1] << 8)
                    | ((uint32_t)lower[kx] << 16)
                    | ((uint32_t)lower[kx + 1] << 24);
            }
        }
    }
    for (; term < 2u * PE_ROWS; ++term)
        words[term] = 0;
}

static void select_chunk(unsigned slot, const uint32_t words[16]) {
    wr(0x000, slot);
    for (unsigned row = 0; row < PE_ROWS; ++row)
        wr(0x100 + 4u * row, words[row]);
    settle();
}

int main(void) {
    uint32_t input_words[32];
    int32_t first_partial[PE_COLS * PE_LANES];

    conv1_full_progress[1] = UINT32_MAX;
    preload_weights();
    conv1_full_progress[0] = 1;

    for (unsigned tile = C1F_TILE_START;
         tile < C1F_TILE_START + C1F_TILE_COUNT; ++tile) {
        unsigned group = tile / POOL_PIXELS;
        unsigned position = tile % POOL_PIXELS;
        unsigned pool_row = position / POOL_SIDE;
        unsigned pool_col = position % POOL_SIDE;
        prepare_window(pool_row, pool_col, input_words);
        conv1_full_progress[0] = 2;

        select_chunk(group * 2u, input_words);
        for (unsigned index = 0; index < PE_COLS * PE_LANES; ++index)
            first_partial[index] = rd(0x400 + 4u * index);

        select_chunk(group * 2u + 1u, &input_words[PE_ROWS]);
        for (unsigned column = 0; column < PE_COLS; ++column) {
            uint32_t quantized[4];
            for (unsigned lane = 0; lane < PE_LANES; ++lane) {
                unsigned index = column * PE_LANES + lane;
                int32_t second = rd(0x400 + 4u * index);
                int64_t acc = (int64_t)first_partial[index] + (int64_t)second
                            + (int64_t)C1F_BIAS[group * PE_COLS + column];
                if (acc < INT32_MIN || acc > INT32_MAX)
                    return 1;
                quantized[lane] = relu_requant_q24((int32_t)acc,
                                                   C1F_REQUANT_MULT);
            }
            uint32_t pooled = maxpool_four(quantized);
#if defined(C1F_INJECT_POOL_ERROR) && C1F_INJECT_POOL_ERROR
            if (tile == C1F_TILE_START && column == 0)
                pooled ^= 1u;
#endif
            unsigned channel = group * PE_COLS + column;
            conv1_full_output[channel * POOL_PIXELS + position] = (uint8_t)pooled;
        }
        conv1_full_progress[1] = tile;
        conv1_full_progress[2] = tile - C1F_TILE_START + 1u;
        conv1_full_progress[3] = (tile - C1F_TILE_START + 1u) * PE_COLS;
    }
    conv1_full_progress[0] = 3;
    __asm__ volatile ("fence iorw, iorw" ::: "memory");
    return 0;
}
