OUTPUT_ARCH(riscv)
ENTRY(_start)
/* The payload executes from DDR; the mapping block comes from the address
 * contract (gateware/machines/ae350_map.py). */
INCLUDE ae350_map.ld
SECTIONS {
    .text : { __text_start = .; KEEP(*(.text.start)) *(.text*) *(.rodata*) . = ALIGN(4); __text_end = .; } > ddr
    .ilm : { . = ALIGN(4); __ilm_start = .; *(.ilm.text*) . = ALIGN(4); __ilm_end = .; } > ilm AT>ddr
    __ilm_load = LOADADDR(.ilm);
    .data : { . = ALIGN(4); __data_start = .; *(.data*) *(.sdata*) . = ALIGN(4); __data_end = .; } > ddr AT>ddr
    __data_load = LOADADDR(.data);
    __global_pointer$ = 0x4000;
    .bss (NOLOAD) : { . = ALIGN(4); __bss_start = .; *(.bss*) *(.sbss*) *(COMMON) . = ALIGN(4); __bss_end = .; } > ddr
    __stack_top = __payload_stack_top;
    ASSERT(__bss_end <= ORIGIN(ddr) + LENGTH(ddr), "Application runtime image exceeds DDR load region")
    /DISCARD/ : { *(.comment*) *(.eh_frame*) }
}
