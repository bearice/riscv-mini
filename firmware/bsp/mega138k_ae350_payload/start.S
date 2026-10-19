.section .text.start,"ax",@progbits
.globl _start
.option norvc
_start:
    csrw mie, zero
    li t0, 8
    csrc mstatus, t0
    .option push
    .option norelax
    la gp, __global_pointer$
    .option pop
    la sp, __stack_top
    la t0, trap_entry
    csrw mtvec, t0
    la t1, __bss_start
    la t2, __bss_end
3:  bgeu t1, t2, 4f
    sw zero, 0(t1)
    addi t1, t1, 4
    j 3b
4:  fence.i
    call main
5:  wfi
    j 5b
.balign 4
trap_entry:
    /* Report the faulting state without touching DDR: the monitor's SRAM
     * stack is not available to the payload, so park the stack in DDR. */
    csrr a0, mcause
    csrr a1, mepc
    csrr a2, mtval
    mv a3, ra
    mv a4, sp
    la sp, __stack_top
    call trap_report
    j 5b
