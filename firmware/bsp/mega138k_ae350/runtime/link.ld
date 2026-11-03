OUTPUT_ARCH(riscv)
ENTRY(_start)
/* The payload executes from DDR; the mapping block comes from the address
 * contract (gateware/machines/mega138k_ae350/address_map.py). */
INCLUDE ae350_map.ld
SECTIONS {
    .text : { __text_start = .; KEEP(*(.text.start)) *(.text*) *(.rodata*) . = ALIGN(4); __text_end = .; } > ddr
    .ilm : { . = ALIGN(4); __ilm_start = .; *(.ilm.text*) . = ALIGN(4); __ilm_end = .; } > ilm AT>ddr
    __ilm_load = LOADADDR(.ilm);
    .data : { . = ALIGN(4); __data_start = .; *(.data*) *(.sdata*) . = ALIGN(4); __data_end = .; } > ddr AT>ddr
    __data_load = LOADADDR(.data);
    __global_pointer$ = 0x4000;
    .bss (NOLOAD) : { . = ALIGN(4); __bss_start = .; *(.bss*) *(.sbss*) *(COMMON) . = ALIGN(4); __bss_end = .; } > bss
    __stack_top = __payload_stack_top;
    /DISCARD/ : { *(.comment*) *(.eh_frame*) }
}
