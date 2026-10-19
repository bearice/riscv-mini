.section .text.start,"ax",@progbits
.globl _start
.option norvc
_start:
    csrw mie, zero
    li t0, 8
    csrc mstatus, t0
    /* Local memories are hard resources, not optional fabric ITCM/DTCM.
       Keep ECC checking/diagnostic reads disabled while seeding RAM; do not
       assume the FPGA bitstream initializes these memories. Base and enable
       are platform configuration, checked before using a stack. */
    csrr t0, 0x7c0
    li t1, 0xfffffc01
    and t0, t0, t1
    la t2, __ilm_base
    ori t2, t2, 1
    bne t0, t2, local_config_error
    csrr t0, 0x7c1
    and t0, t0, t1
    la t2, __dlm_base
    ori t2, t2, 1
    bne t0, t2, local_config_error
    li t0, 14
    csrc 0x7c0, t0
    csrc 0x7c1, t0
    /* Destructive address-dependent tests before any stack or live data.
       Both polarities cover every word and expose address aliasing. */
    la a0, __ilm_base
    la a1, __ilm_limit
    call local_ram_post
    la a0, __dlm_base
    la a1, __dlm_limit
    call local_ram_post
    la t1, __ilm_base
    la t2, __ilm_limit
6:  sw zero, 0(t1)
    addi t1, t1, 4
    bltu t1, t2, 6b
    la t1, __dlm_base
    la t2, __dlm_limit
7:  sw zero, 0(t1)
    addi t1, t1, 4
    bltu t1, t2, 7b
    la t0, __ilm_load
    la t1, __ilm_start
    la t2, __ilm_end
8:  bgeu t1, t2, 9f
    lw t3, 0(t0)
    sw t3, 0(t1)
    addi t0, t0, 4
    addi t1, t1, 4
    j 8b
9:  fence.i
    .option push
    .option norelax
    la gp, __global_pointer$
    .option pop
    la sp, __stack_top
    la t0, trap_entry
    csrw mtvec, t0
    la t0, __data_load
    la t1, __data_start
    la t2, __data_end
1:  bgeu t1, t2, 2f
    lw t3, 0(t0)
    sw t3, 0(t1)
    addi t0, t0, 4
    addi t1, t1, 4
    j 1b
2:  la t1, __bss_start
    la t2, __bss_end
3:  bgeu t1, t2, 4f
    sw zero, 0(t1)
    addi t1, t1, 4
    j 3b
4:  fence.i
    /* Bootloader starts with caches disabled before the DDR POST and upload. */
    csrw 0x7ca, zero
    fence.i
    call main
5:  wfi
    j 5b
.globl local_config_error
local_config_error:
    /* Stack-free ROM park; DPC and t0/t2 identify a configuration mismatch. */
    j local_config_error
.globl local_memory_error
local_memory_error:
    /* t0=address, t1=expected, t2=actual; debugger can inspect without RAM. */
    j local_memory_error
local_ram_post:
    li t4, 0x96a55a69
    li a2, 2
10: mv t0, a0
11: xor t1, t0, t4
    sw t1, 0(t0)
    addi t0, t0, 4
    bltu t0, a1, 11b
    mv t0, a0
12: xor t1, t0, t4
    lw t2, 0(t0)
    bne t1, t2, local_memory_error
    addi t0, t0, 4
    bltu t0, a1, 12b
    not t4, t4
    addi a2, a2, -1
    bnez a2, 10b
    ret
.balign 4
trap_entry:
    csrr a0, mcause
    csrr a1, mepc
    csrr a2, mtval
    mv a3, ra
    mv a4, sp
    la sp, __stack_top
    call trap_report
    j 5b
