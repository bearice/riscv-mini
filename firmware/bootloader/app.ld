OUTPUT_ARCH(riscv)
ENTRY(_start)
MEMORY { DDR (rwx) : ORIGIN = 0x40800000, LENGTH = 4M }
SECTIONS {
    .text : { KEEP(*(.text.start)) *(.text .text.*) } > DDR
    .rodata : { *(.rodata .rodata.*) . = ALIGN(4); } > DDR
    .data : {
        . = ALIGN(4); _sdata = .;
        *(.data .data.* .sdata .sdata.*)
        . = ALIGN(4); _edata = .;
    } > DDR
    _sidata = LOADADDR(.data);
    __global_pointer$ = _sdata + 0x800;
    .bss (NOLOAD) : {
        . = ALIGN(4); _sbss = .;
        *(.bss .bss.* .sbss .sbss.*) *(COMMON)
        . = ALIGN(4); _ebss = .;
    } > DDR
    _stack_top = ORIGIN(DDR) + LENGTH(DDR);
    ASSERT(_ebss <= _stack_top - 65536, "Reserve 64 KiB DDR stack")
    /DISCARD/ : { *(.comment) *(.eh_frame*) }
}
