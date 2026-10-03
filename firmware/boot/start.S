.section .text.start, "ax", @progbits
.global _start
_start:
    csrw mie, zero
    csrci mstatus, 8
#if MINI_FEATURE_MMU
    csrw satp, zero
#endif
#if MINI_FEATURE_FPU
    li t0, 0x6000
    csrs mstatus, t0
#endif
#if MINI_CPU_DCACHE
    .word 0x0000500f
#endif
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
