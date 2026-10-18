OUTPUT_ARCH(riscv)
ENTRY(_start)
MEMORY {
    rom (rx) : ORIGIN = 0x80000000, LENGTH = 8K
    ram (rw) : ORIGIN = 0x00000000, LENGTH = 8K
}
SECTIONS {
    .text : { KEEP(*(.text.start)) *(.text*) *(.rodata*) . = ALIGN(4); } > rom
    .data : { . = ALIGN(4); __data_start = .; *(.data*) *(.sdata*) . = ALIGN(4); __data_end = .; } > ram AT>rom
    __data_load = LOADADDR(.data);
    __global_pointer$ = ORIGIN(ram) + 0x800;
    .bss (NOLOAD) : { . = ALIGN(4); __bss_start = .; *(.bss*) *(.sbss*) *(COMMON) . = ALIGN(4); __bss_end = .; } > ram
    __stack_top = ORIGIN(ram) + LENGTH(ram);
    ASSERT(__bss_end <= __stack_top - 2048, "P1 requires at least 2KiB stack")
    /DISCARD/ : { *(.comment*) *(.eh_frame*) }
}
