.section .text.start, "ax", @progbits
.global _start
.type _start, @function
#if MINI_BOOTLOADER
#include "boot_regs.h"
#endif
_start:
    csrw mie, zero
    csrci mstatus, 8
#if MINI_FEATURE_MMU
    csrw satp, zero
#endif
#if MINI_FEATURE_FPU && !MINI_BOOTLOADER
    li t0, 0x6000
    csrs mstatus, t0
#endif
#if MINI_CPU_DCACHE
    .word 0x0000500f
#endif
#if MINI_BOOTLOADER
    /* No stack, global data or DDR access until the hardware owns training. */
    li t0, MINI_DDR_BOOT_STATUS
    li t3, 120000000
.Lddr_wait:
    lw t1, 0(t0)
    andi t2, t1, 2
    bnez t2, .Lddr_failed
    andi t2, t1, 1
    bnez t2, .Lddr_ready
    addi t3, t3, -1
    bnez t3, .Lddr_wait
.Lddr_failed:
    /* Status fits 16 bits. Emit it without a stack or any DDR access. */
    mv t4, t1
    li t5, 4
    li t0, MINI_UART_TXFULL
    li t1, MINI_UART_RXTX
.Lddr_status_hex:
    srli a1, t4, 12
    andi a1, a1, 15
    li a0, 10
    bltu a1, a0, .Lddr_status_digit
    addi a1, a1, 7
.Lddr_status_digit:
    addi a1, a1, 48
.Lddr_status_tx:
    lw t2, 0(t0)
    bnez t2, .Lddr_status_tx
    sw a1, 0(t1)
    slli t4, t4, 4
    addi t5, t5, -1
    bnez t5, .Lddr_status_hex
    la a0, .Lddr_error
.Lddr_error_next:
    lbu a1, 0(a0)
    beqz a1, .Lddr_halt
.Lddr_tx_wait:
    lw t2, 0(t0)
    bnez t2, .Lddr_tx_wait
    sw a1, 0(t1)
    addi a0, a0, 1
    j .Lddr_error_next
.Lddr_halt:
    j .Lddr_halt
.Lddr_ready:
    fence rw, rw
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
.size _start, .-_start
#if MINI_BOOTLOADER
.section .rodata
.Lddr_error:
    .asciz "\r\nERR DDR INIT/TIMEOUT; reset\r\n"
#endif
