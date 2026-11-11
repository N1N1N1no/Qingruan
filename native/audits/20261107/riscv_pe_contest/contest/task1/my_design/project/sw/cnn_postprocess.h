#ifndef CNN_POSTPROCESS_H
#define CNN_POSTPROCESS_H

#include <stdint.h>

/* Reference INT8 activation rule: ReLU, Q24 rounding, then clip to 0..127.
 * Convert only positive values to unsigned, so no signed negative shift is
 * involved. For every positive int32_t acc and uint32_t multiplier, the
 * product plus the Q24 rounding constant fits in uint64_t.
 */
static inline uint32_t relu_requant_q24(int32_t acc, uint32_t multiplier) {
    if (acc <= 0)
        return 0;
    uint64_t scaled = (uint64_t)(uint32_t)acc * (uint64_t)multiplier;
    uint64_t quantized = (scaled + (UINT64_C(1) << 23)) >> 24;
    return quantized > 127 ? 127u : (uint32_t)quantized;
}

/* One 2x2 pooling window, ordered top-left, top-right, bottom-left,
 * bottom-right. Callers may read volatile storage into a local array first.
 */
static inline uint32_t maxpool_four(const uint32_t values[4]) {
    uint32_t maximum = values[0];
    for (unsigned i = 1; i < 4; ++i)
        if (values[i] > maximum)
            maximum = values[i];
    return maximum;
}

#endif
