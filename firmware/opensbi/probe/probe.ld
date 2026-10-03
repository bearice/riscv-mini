ENTRY(_start)
SECTIONS {
 . = 0x41100000;
 .text : { *(.text.start) *(.text*) }
 .rodata : { *(.rodata*) }
 .data : { *(.data*) *(.sdata*) }
 __global_pointer$ = . + 0x800;
 . = ALIGN(16); _bss_start = .;
 .bss : { *(.bss*) *(.sbss*) *(COMMON) }
 . = ALIGN(16); _bss_end = .;
 . += 16384; _stack_top = .;
 /DISCARD/ : { *(.eh_frame*) *(.comment*) }
}
