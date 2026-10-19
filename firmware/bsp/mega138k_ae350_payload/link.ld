OUTPUT_ARCH(riscv)
ENTRY(_start)
/* The payload executes from DDR; the mapping block comes from the address
 * contract (gateware/machines/ae350_map.py). */
INCLUDE ae350_map.ld
SECTIONS {
    .text : { __text_start = .; KEEP(*(.text.start)) *(.text*) *(.rodata*) . = ALIGN(4); __text_end = .; } > ddr
    .data : { . = ALIGN(4); __data_start = .; *(.data*) *(.sdata*) . = ALIGN(4); __data_end = .; } > ddr AT>ddr
    __data_load = LOADADDR(.data);
    __global_pointer$ = 0x4000;
    .bss (NOLOAD) : { . = ALIGN(4); __bss_start = .; *(.bss*) *(.sbss*) *(COMMON) . = ALIGN(4); __bss_end = .; } > ddr
    __stack_top = 0x80000;
    ASSERT(__bss_end + 4096 <= __stack_top, "P2 payload requires at least 4KiB stack")
    /DISCARD/ : { *(.comment*) *(.eh_frame*) }
}
