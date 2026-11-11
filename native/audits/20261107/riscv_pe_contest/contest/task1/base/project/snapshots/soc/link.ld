/*
 Copyright 2019-2026 Western Digital Corporation or its affiliates.
 Copyright 2022-2026 Antmicro <www.antmicro.com>

 SPDX-License-Identifier: Apache-2.0
 Licensed under the Apache License, Version 2.0, see LICENSE for details.

 This is an automatically generated file by chens on Tue Oct 13 04:12:23 AM PDT 2026

 cmd:    veer -snapshot=soc -set=build_axi4 -set=iccm_enable=0 -set=dccm_enable=0 -set=icache_enable=0 -set=reset_vec=0x80000000 

*/
OUTPUT_ARCH( "riscv" )
ENTRY(_start)

SECTIONS
{
  . = 0x80000000;
  .text.init .  : { *(.text.init) }
  
  .text . : { *(.text) }
  _end = .;
  . = 0xd0580000;
  .data.io .  : { *(.data.io) }
    . = 0xc0580000 ;
  .data  :  ALIGN(0x800) { *(.*data) *(.rodata*)}
  .bss :  {BSS_START = .; *(.*bss)}
  BSS_END = .;
  STACK = ALIGN(16) + 0x1000;
  
  
}

