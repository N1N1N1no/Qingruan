.section .text.init
.globl _start
_start:
    .option push
    .option norelax
    la gp, __global_pointer$
    .option pop
    la sp, __stack_top
    la t0, trap
    csrw mtvec, t0
    # Mark PE region 9 and console region D as side-effect MMIO.
    # Region 8 holds ordinary instruction/data RAM; caches are disabled.
    li t0, 0x08080000
    csrw 0x7c0, t0
    la t0, __bss_start
    la t1, __bss_end
1:  bgeu t0, t1, 2f
    sw zero, 0(t0)
    addi t0, t0, 4
    j 1b
2:  call main
    bnez a0, fail
    li t1, 0x600dcafe
    j finish
fail:
    li t1, 0xbad00000
    or t1, t1, a0
finish:
    li t0, 0xd0580004
    sw t1, 0(t0)
3:  j 3b
.balign 4
trap:
    csrr a0, mcause
    ori a0, a0, 0x100
    j fail
