OUTPUT_ARCH(riscv)
ENTRY(_start)
/* Generated from gateware/machines/mega138k_ae350/address_map.py: MEMORY regions and the
 * hard local memory and explicit stack regions. */
INCLUDE ae350_boot_contract.ld
SECTIONS {
    .text : { KEEP(*(.text.start)) . = ALIGN(4); } > rom
    /* Run the loader in the existing hard ILM, including UART polling and CRC.
       Its source image remains in the independent XIP partition. */
    .ilm : { . = ALIGN(4); __ilm_start = .; *(.text*) *(.rodata*) *(.ilm.text*) . = ALIGN(4); __ilm_end = .; } > ilm AT>rom
    __ilm_load = LOADADDR(.ilm);
    .data : { . = ALIGN(4); __data_start = .; *(.data*) *(.sdata*) . = ALIGN(4); __data_end = .; } > dlm AT>rom
    __data_load = LOADADDR(.data);
    __global_pointer$ = ORIGIN(dlm) + 0x800;
    .bss (NOLOAD) : { . = ALIGN(4); __bss_start = .; *(.bss*) *(.sbss*) *(COMMON) . = ALIGN(4); __bss_end = .; } > dlm
    __stack_top = __local_stack_top;
    ASSERT(__bss_end <= __local_stack_bottom, "ROM bss overlaps the DLM stack")
    ASSERT(LOADADDR(.data) + SIZEOF(.data) <= ORIGIN(rom) + LENGTH(rom), "ROM load image overlaps status")
    /DISCARD/ : { *(.comment*) *(.eh_frame*) }
}
