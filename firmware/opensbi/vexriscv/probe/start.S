.section .text.start,"ax"
.global _start
_start:
    csrw sscratch,zero
    la sp, _stack_top
    .option push
    .option norelax
    la gp, __global_pointer$
    .option pop
    la t0, _bss_start
    la t1, _bss_end
1:  bgeu t0,t1,2f
    sw zero,0(t0)
    addi t0,t0,4
    j 1b
2:  call probe_main
3:  wfi
    j 3b
.global probe_trap
probe_trap:
    csrrw sp,sscratch,sp
    bnez sp,4f
    csrrw sp,sscratch,sp
4:  addi sp,sp,-128
    sw ra,0(sp)
    sw gp,4(sp)
    sw tp,8(sp)
    sw t0,12(sp)
    sw t1,16(sp)
    sw t2,20(sp)
    sw a0,24(sp)
    sw a1,28(sp)
    sw a2,32(sp)
    sw a3,36(sp)
    sw a4,40(sp)
    sw a5,44(sp)
    sw a6,48(sp)
    sw a7,52(sp)
    sw t3,56(sp)
    sw t4,60(sp)
    sw t5,64(sp)
    sw t6,68(sp)
    mv a0,sp
    call probe_dispatch
    lw ra,0(sp)
    lw gp,4(sp)
    lw tp,8(sp)
    lw t1,16(sp)
    lw t2,20(sp)
    lw a0,24(sp)
    lw a1,28(sp)
    lw a2,32(sp)
    lw a3,36(sp)
    lw a4,40(sp)
    lw a5,44(sp)
    lw a6,48(sp)
    lw a7,52(sp)
    lw t3,56(sp)
    lw t4,60(sp)
    lw t5,64(sp)
    lw t6,68(sp)
    csrr t0,sstatus
    andi t0,t0,256
    bnez t0,5f
    lw t0,12(sp)
    addi sp,sp,128
    csrrw sp,sscratch,sp
    sret
5:  lw t0,12(sp)
    addi sp,sp,128
    sret

.global probe_enter_user
probe_enter_user:
    addi sp,sp,-16
    sw ra,0(sp)
    csrw sscratch,sp
    li t0,0x00400000
    csrw sepc,t0
    li t0,256
    csrc sstatus,t0
    li sp,0x00403000
    sret
.global probe_user_return
probe_user_return:
    csrw sscratch,zero
    lw ra,0(sp)
    addi sp,sp,16
    ret
.section .rodata.user,"a"
.balign 4
.global probe_user_code,probe_user_end
probe_user_code:
    li t0,0x00402000
    li t1,0x12345678
    sw t1,0(t0)
    rdtime t1
    sw t1,4(t0)
    li t0,0x01100000
    lw t1,0(t0)
    li t0,0xf0000000
    lw t1,0(t0)
    li t0,0x00400000
    sw zero,0(t0)
    ecall
1:  j 1b
probe_user_end:
