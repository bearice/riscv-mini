.section .text.start, "ax", @progbits
.global _start
_start:
    la sp, _stack_top
    .option push
    .option norelax
    la gp, __global_pointer$
    .option pop
    la a0, _sidata
    la a1, _sdata
    la a2, _edata
1:
    bgeu a1, a2, 2f
    lw t0, 0(a0)
    sw t0, 0(a1)
    addi a0, a0, 4
    addi a1, a1, 4
    j 1b
2:
    la a0, _sbss
    la a1, _ebss
3:
    bgeu a0, a1, 4f
    sw zero, 0(a0)
    addi a0, a0, 4
    j 3b
4:
    call main
5:
    j 5b
#ifdef MINI_DDR
.section .text.ddr_call,"ax",@progbits
.global ddr_call
ddr_call:
    addi sp, sp, -16
    sw ra, 12(sp)
    sw s0, 8(sp)
    mv s0, sp
    mv sp, a1
    jalr ra, a0, 0
    mv sp, s0
    lw s0, 8(sp)
    lw ra, 12(sp)
    addi sp, sp, 16
    ret
#endif
