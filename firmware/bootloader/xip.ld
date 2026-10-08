OUTPUT_ARCH(riscv)
ENTRY(_start)
MEMORY {
    ROM (rx) : ORIGIN = BOOT_XIP_ORIGIN, LENGTH = BOOT_XIP_SIZE
    BOOT_RAM (rwx) : ORIGIN = 0x007ff000, LENGTH = 4K
}
SECTIONS {
    .text : { KEEP(*(.text.start)) *(.text .text.*) } > ROM
    .rodata : { *(.rodata .rodata.*) . = ALIGN(4); } > ROM
    .data : {
        . = ALIGN(4); _sdata = .;
        *(.data .data.* .sdata .sdata.*)
        . = ALIGN(4); _edata = .;
    } > BOOT_RAM AT > ROM
    _sidata = LOADADDR(.data);
    __global_pointer$ = _sdata + 0x800;
    .bss (NOLOAD) : {
        . = ALIGN(4); _sbss = .;
        *(.bss .bss.* .sbss .sbss.*) *(COMMON)
        . = ALIGN(4); _ebss = .;
    } > BOOT_RAM
    _stack_top = ORIGIN(BOOT_RAM) + LENGTH(BOOT_RAM);
    ASSERT(_ebss <= _stack_top - 2048, "Reserve at least 2 KiB boot stack")
    /DISCARD/ : { *(.comment) *(.eh_frame*) }
}
