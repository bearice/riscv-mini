OUTPUT_ARCH(riscv)
ENTRY(_start)
/* Generated from gateware/machines/ae350_map.py: MEMORY regions and the
 * hard local memory and explicit stack regions. */
INCLUDE ae350_map.ld
SECTIONS {
    .text : { KEEP(*(.text.start)) *(.text*) *(.rodata*) . = ALIGN(4); } > rom
    .ilm : { . = ALIGN(4); __ilm_start = .; *(.ilm.text*) . = ALIGN(4); __ilm_end = .; } > ilm AT>rom
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
