OUTPUT_ARCH(riscv)
ENTRY(_start)
/* Generated from gateware/machines/ae350_map.py: MEMORY regions and the
 * reserved SRAM blocks (mailbox, results, params, stack). */
INCLUDE ae350_map.ld
SECTIONS {
    .text : { KEEP(*(.text.start)) *(.text*) *(.rodata*) . = ALIGN(4); } > rom
    .data : { . = ALIGN(4); __data_start = .; *(.data*) *(.sdata*) . = ALIGN(4); __data_end = .; } > ram AT>rom
    __data_load = LOADADDR(.data);
    __global_pointer$ = ORIGIN(ram) + 0x800;
    .bss (NOLOAD) : { . = ALIGN(4); __bss_start = .; *(.bss*) *(.sbss*) *(COMMON) . = ALIGN(4); __bss_end = .; } > ram
    ASSERT(__data_end <= __mailbox_base, "ROM data overlaps the mailbox")
    ASSERT(__bss_end <= __mailbox_base, "ROM bss overlaps the mailbox")
    ASSERT(SIZEOF(.text) <= 0x1fc0, "ROM text must stay below the status window")
    /DISCARD/ : { *(.comment*) *(.eh_frame*) }
}
