#include <stdint.h>

#define PE_BASE 0x90000000u
static inline void wr(unsigned off, uint32_t v) {
    *(volatile uint32_t *)(PE_BASE + off) = v;
    __asm__ volatile ("fence iorw, iorw" ::: "memory");
}
static inline uint32_t rd(unsigned off) {
    return *(volatile uint32_t *)(PE_BASE + off);
}
static void settle(void) {
    // Static inputs, no pipeline backpressure: ample time for 16 rows/columns.
    for (volatile unsigned i = 0; i < 128; ++i)
        __asm__ volatile ("nop");
}
int main(void) {
    wr(0x000, 0);                 // Weight address 0
    wr(0x008, 0xffff);            // Enable weight memories in all rows
    for (unsigned i = 0; i < 4; ++i)
        wr(0x020 + 4*i, 0x01010101); // 16 column weights, each = 1
    wr(0x010, 0xffff);            // One write pulse to all rows
    // Zero top-of-array partial sums; include both halves of every AXI word.
    for (unsigned i = 0; i < 64; ++i) wr(0x200 + 4*i, 0);
    for (unsigned row = 0; row < 16; ++row)
        wr(0x100 + 4*row, 0x05040302);
    settle();
    for (unsigned col = 0; col < 16; ++col)
        for (unsigned lane = 0; lane < 4; ++lane)
            if (rd(0x400 + 16*col + 4*lane) != 16*(lane+2))
                return 1 + 4*col + lane;
    return 0;
}
