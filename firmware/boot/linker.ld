OUTPUT_ARCH(riscv)
ENTRY(_start)
MEMORY {
    ROM (rx) : ORIGIN = 0x00000000, LENGTH = 32K
    SRAM (rwx) : ORIGIN = 0x10000000, LENGTH = 16K
}
SECTIONS {
    .text : { KEEP(*(.text.start)) *(.text .text.*) } > ROM
    .rodata : { *(.rodata .rodata.*) . = ALIGN(4); } > ROM
    .data : {
        . = ALIGN(4); _sdata = .;
        *(.data .data.* .sdata .sdata.*)
        . = ALIGN(4); _edata = .;
    } > SRAM AT > ROM
    _sidata = LOADADDR(.data);
    __global_pointer$ = _sdata + 0x800;
    .bss (NOLOAD) : {
        . = ALIGN(4); _sbss = .;
        *(.bss .bss.* .sbss .sbss.*) *(COMMON)
        . = ALIGN(4); _ebss = .;
    } > SRAM
    _stack_top = ORIGIN(SRAM) + LENGTH(SRAM);
    ASSERT(_ebss <= _stack_top - 2048, "Reserve at least 2 KiB of stack")
    /DISCARD/ : { *(.comment) *(.eh_frame*) }
}
