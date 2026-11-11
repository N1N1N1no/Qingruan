#include <stdint.h>

#include "conv2_tile_data.h"
#include "cnn_postprocess.h"

#if C2T_REQUANT_SHIFT != 24
#error "cnn_postprocess.h requires the model's Q24 quantization"
#endif

#define PE_BASE 0x90000000u
#define PE_ROWS 16u
#define PE_COLS 16u
#define PE_LANES 4u
#define INPUT_CHANNELS 32u
#define OUTPUT_CHANNELS 64u
#define TERMS (INPUT_CHANNELS * 3u * 3u)
#define GROUPS (OUTPUT_CHANNELS / PE_COLS)
#define BATCHES (TERMS / PE_ROWS)
#define TILE_OUTPUTS (OUTPUT_CHANNELS * PE_LANES)

enum {
    PARTIAL_WORDS = GROUPS * BATCHES * PE_COLS * PE_LANES,
    RAW_OFFSET = PARTIAL_WORDS,
    REQUANT_OFFSET = RAW_OFFSET + TILE_OUTPUTS,
    POOL_OFFSET = REQUANT_OFFSET + TILE_OUTPUTS,
    RESULT_WORDS = POOL_OFFSET + OUTPUT_CHANNELS
};

_Static_assert(TERMS % PE_ROWS == 0, "Conv2 uses eighteen complete K batches");
_Static_assert(C2T_ROW >= 0 && C2T_ROW <= 60 && C2T_ROW % 2 == 0,
               "Conv2 pool window row must be even and within 0..60");
_Static_assert(C2T_COL >= 0 && C2T_COL <= 60 && C2T_COL % 2 == 0,
               "Conv2 pool window column must be even and within 0..60");
_Static_assert(C2T_FAULT_TERM >= 0 && C2T_FAULT_TERM < TERMS,
               "The fault-injection term must be in the actual K window");

/* All expected results live exclusively in the independent test environment.
 * [0..4607]: partials, group -> batch -> column -> lane;
 * [4608..4863]: raw accumulators including bias, channel -> lane;
 * [4864..5119]: ReLU/Q24 uint8 values stored as int32 words;
 * [5120..5183]: one pooled value for each of the 64 output channels.
 */
volatile int32_t conv2_tile_results[RESULT_WORDS];

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
    /* Hold all inputs static while horizontal data and vertical partial sums
     * propagate. Preserve the full Conv1 driver's conservative wait.
     */
    __asm__ volatile (".rept 64\n\tnop\n\t.endr" ::: "memory");
}

static void prepare_window(uint32_t input_words[TERMS]) {
    unsigned term = 0;
    for (unsigned channel = 0; channel < INPUT_CHANNELS; ++channel) {
        for (unsigned ky = 0; ky < 3; ++ky) {
            const uint8_t *upper = &C2T_INPUT_HW[channel][C2T_ROW + ky][C2T_COL];
            const uint8_t *lower = &C2T_INPUT_HW[channel][C2T_ROW + ky + 1][C2T_COL];
            for (unsigned kx = 0; kx < 3; ++kx) {
                /* The input is the preceding real CPU Pool1 output, CHW.
                 * Four lanes cover top-left/right, bottom-left/right.
                 */
                input_words[term++] = (uint32_t)upper[kx]
                    | ((uint32_t)upper[kx + 1] << 8)
                    | ((uint32_t)lower[kx] << 16)
                    | ((uint32_t)lower[kx + 1] << 24);
            }
        }
    }
}

static void run_batch(const uint32_t input_words[TERMS],
                      unsigned group, unsigned batch) {
    unsigned first = batch * PE_ROWS;
    /* Reuse SRAM slot zero: load one K slice for sixteen output channels.
     * All sixteen rows contain valid terms; 288 / 16 is exactly eighteen.
     */
    for (unsigned row = 0; row < PE_ROWS; ++row) {
        unsigned term = first + row;
        for (unsigned word = 0; word < PE_COLS / 4; ++word) {
            uint32_t packed = pack_four(
                &C2T_WEIGHT_HW[term][group * PE_COLS + word * 4u]);
#if defined(C2T_INJECT_WEIGHT_ERROR) && C2T_INJECT_WEIGHT_ERROR
            if (group == 0 && term == C2T_FAULT_TERM && word == 0)
                packed ^= 1u;
#endif
            wr(0x020 + 4u * word, packed);
        }
        wr(0x010, 1u << row);
    }
    for (unsigned row = 0; row < PE_ROWS; ++row) {
        uint32_t packed = input_words[first + row];
#if defined(C2T_INJECT_INPUT_ERROR) && C2T_INJECT_INPUT_ERROR
        if (group == 0 && first + row == C2T_FAULT_TERM)
            packed ^= 1u;
#endif
        wr(0x100 + 4u * row, packed);
    }
    settle();

    unsigned base = (group * BATCHES + batch) * PE_COLS * PE_LANES;
    for (unsigned index = 0; index < PE_COLS * PE_LANES; ++index)
        conv2_tile_results[base + index] = rd(0x400 + 4u * index);
}

int main(void) {
    uint32_t input_words[TERMS];
    prepare_window(input_words);

    wr(0x000, 0);
    wr(0x008, 0xffff);
    for (unsigned word = 0; word < PE_COLS * PE_LANES; ++word)
        wr(0x200 + 4u * word, 0);

    for (unsigned group = 0; group < GROUPS; ++group)
        for (unsigned batch = 0; batch < BATCHES; ++batch)
            run_batch(input_words, group, batch);

    for (unsigned channel = 0; channel < OUTPUT_CHANNELS; ++channel) {
        unsigned group = channel / PE_COLS;
        unsigned column = channel % PE_COLS;
        for (unsigned lane = 0; lane < PE_LANES; ++lane) {
            int64_t acc = (int64_t)C2T_BIAS[channel];
            for (unsigned batch = 0; batch < BATCHES; ++batch) {
                unsigned index = (group * BATCHES + batch) * PE_COLS * PE_LANES
                               + column * PE_LANES + lane;
                acc += (int64_t)conv2_tile_results[index];
            }
            if (acc < INT32_MIN || acc > INT32_MAX)
                return 1;
            conv2_tile_results[RAW_OFFSET + channel * PE_LANES + lane] = (int32_t)acc;
        }
    }

    for (unsigned index = 0; index < TILE_OUTPUTS; ++index) {
        int32_t raw = conv2_tile_results[RAW_OFFSET + index];
        uint32_t quantized = relu_requant_q24(raw, C2T_REQUANT_MULT);
#if defined(C2T_INJECT_REQUANT_ERROR) && C2T_INJECT_REQUANT_ERROR
        if (index == 0)
            quantized ^= 1u;
#endif
        conv2_tile_results[REQUANT_OFFSET + index] = (int32_t)quantized;
    }
    for (unsigned channel = 0; channel < OUTPUT_CHANNELS; ++channel) {
        uint32_t window[4];
        for (unsigned lane = 0; lane < PE_LANES; ++lane)
            window[lane] = (uint32_t)conv2_tile_results[
                REQUANT_OFFSET + channel * PE_LANES + lane];
        uint32_t pooled = maxpool_four(window);
#if defined(C2T_INJECT_POOL_ERROR) && C2T_INJECT_POOL_ERROR
        if (channel == 0)
            pooled ^= 1u;
#endif
        conv2_tile_results[POOL_OFFSET + channel] = (int32_t)pooled;
    }
    __asm__ volatile ("fence iorw, iorw" ::: "memory");
    return 0;
}
