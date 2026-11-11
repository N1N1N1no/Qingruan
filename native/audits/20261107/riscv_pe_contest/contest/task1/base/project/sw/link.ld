OUTPUT_ARCH(riscv)
ENTRY(_start)
MEMORY { RAM (rwx) : ORIGIN = 0x80000000, LENGTH = 256K }
SECTIONS {
    .text : { KEEP(*(.text.init)) *(.text*) } > RAM
    .rodata : { *(.rodata*) *(.srodata*) } > RAM
    .data : { . = ALIGN(4); *(.data*) *(.sdata*) } > RAM
    PROVIDE(__global_pointer$ = ADDR(.data) + 0x800);
    .bss (NOLOAD) : {
        . = ALIGN(4); __bss_start = .;
        *(.bss*) *(.sbss*) *(COMMON)
        . = ALIGN(4); __bss_end = .;
    } > RAM
    __stack_top = ORIGIN(RAM) + LENGTH(RAM) - 16;
    ASSERT(__bss_end < __stack_top - 4096, "Firmware overlaps stack")
}
