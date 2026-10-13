OUTPUT_ARCH(riscv)
ENTRY(_payload_start)
MEMORY { PAYLOAD (rwx) : ORIGIN = 0x01000000, LENGTH = 110M }
SECTIONS {
    .text : { KEEP(*(.text.start)) *(.text .text.*) } > PAYLOAD
    .rodata : { *(.rodata .rodata.*) } > PAYLOAD
    .preinit_array : { __preinit_array_start = .; KEEP(*(.preinit_array*)) __preinit_array_end = .; } > PAYLOAD
    .init_array : { __init_array_start = .; KEEP(*(SORT(.init_array.*))) KEEP(*(.init_array)) __init_array_end = .; } > PAYLOAD
    .fini_array : { __fini_array_start = .; KEEP(*(SORT(.fini_array.*))) KEEP(*(.fini_array)) __fini_array_end = .; } > PAYLOAD
    .data : { . = ALIGN(4); *(.data .data.* .sdata .sdata.*) . = ALIGN(4); } > PAYLOAD
    __global_pointer$ = . + 0x800;
    _file_end = .;
    .bss (NOLOAD) : { . = ALIGN(4); *(.bss .bss.* .sbss .sbss.*) *(COMMON) . = ALIGN(4); } > PAYLOAD
    _memory_end = .;
    ASSERT(_memory_end <= 0x07df0000, "Reserve 64 KiB payload stack")
    /DISCARD/ : { *(.comment) *(.eh_frame*) }
}
