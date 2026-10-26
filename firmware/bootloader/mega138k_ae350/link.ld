OUTPUT_ARCH(riscv)
ENTRY(_start)
/* Flash capacity and hard local memory regions from the boot contract. */
INCLUDE ae350_boot_contract.ld
SECTIONS {
    .text : { KEEP(*(.text.start)) *(.text*) *(.rodata*) *(.srodata*) . = ALIGN(4); } > rom
    /* Ordinary code/constants use the Flash region; explicitly hot sections
       are copied to hard ILM by the reset code. */
    .ilm : { . = ALIGN(4); __ilm_start = .; *(.ilm.text*) *(.ilm.rodata*) . = ALIGN(4); __ilm_end = .; } > ilm AT>rom
    __ilm_load = LOADADDR(.ilm);
    .data : { . = ALIGN(4); __data_start = .; *(.data*) *(.sdata*) . = ALIGN(4); __data_end = .; } > dlm AT>rom
    __data_load = LOADADDR(.data);
    __global_pointer$ = ORIGIN(dlm) + 0x800;
    .bss (NOLOAD) : { . = ALIGN(4); __bss_start = .; *(.bss*) *(.sbss*) *(COMMON) . = ALIGN(4); __bss_end = .; } > dlm
    __stack_top = __local_stack_top;
    ASSERT(__bss_end <= __local_stack_bottom, "ROM bss overlaps the DLM stack")
    ASSERT(LOADADDR(.data) + SIZEOF(.data) <= ORIGIN(rom) + LENGTH(rom), "Flash firmware region overflow")
    /DISCARD/ : { *(.comment*) *(.eh_frame*) }
}
