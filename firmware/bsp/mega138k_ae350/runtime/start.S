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
    /* After UART CRC/handoff the application owns ILM. Its load image is
       part of the normal DDR binary, not separately injected by the host. */
    la t0, __ilm_load
    la t1, __ilm_start
    la t2, __ilm_end
1:  bgeu t1, t2, 2f
    lw t3, 0(t0)
    sw t3, 0(t1)
    addi t0, t0, 4
    addi t1, t1, 4
    j 1b
2:
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
    addi sp, sp, -16
    sw t0, 0(sp)
    csrr t0, mcause
    bltz t0, irq_entry
    lw t0, 0(sp)
    addi sp, sp, 16
exception_entry:
    /* Reset the application's private DLM stack before reporting a fault. */
    csrr a0, mcause
    csrr a1, mepc
    csrr a2, mtval
    mv a3, ra
    mv a4, sp
    la sp, __stack_top
    call trap_report
    j 5b
irq_entry:
    /* Original t0 is at 0(sp). sp is 16 below the interrupted stack. */
    addi sp, sp, -64
    sw ra, 0(sp)
    sw t1, 4(sp)
    sw t2, 8(sp)
    sw t3, 12(sp)
    sw t4, 16(sp)
    sw t5, 20(sp)
    sw t6, 24(sp)
    sw a0, 28(sp)
    sw a1, 32(sp)
    sw a2, 36(sp)
    sw a3, 40(sp)
    sw a4, 44(sp)
    sw a5, 48(sp)
    sw a6, 52(sp)
    sw a7, 56(sp)
    csrr a0, mcause
    call irq_dispatch
    lw ra, 0(sp)
    lw t1, 4(sp)
    lw t2, 8(sp)
    lw t3, 12(sp)
    lw t4, 16(sp)
    lw t5, 20(sp)
    lw t6, 24(sp)
    lw a0, 28(sp)
    lw a1, 32(sp)
    lw a2, 36(sp)
    lw a3, 40(sp)
    lw a4, 44(sp)
    lw a5, 48(sp)
    lw a6, 52(sp)
    lw a7, 56(sp)
    lw t0, 64(sp)
    addi sp, sp, 80
    mret
