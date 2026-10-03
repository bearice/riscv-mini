.section .text.bios_enter,"ax",@progbits
.global bios_enter
/* entry in a0, BIOS info in a1, payload stack in a2; payload entry(info).
 * Preserve BIOS callee-saved state so an ordinary C payload may return. */
bios_enter:
    addi sp,sp,-64
    sw ra,0(sp)
    sw gp,4(sp)
    sw s0,8(sp)
    sw s1,12(sp)
    sw s2,16(sp)
    sw s3,20(sp)
    sw s4,24(sp)
    sw s5,28(sp)
    sw s6,32(sp)
    sw s7,36(sp)
    sw s8,40(sp)
    sw s9,44(sp)
    sw s10,48(sp)
    sw s11,52(sp)
    mv s0,sp
    mv t0,a0
    mv a0,a1
    mv sp,a2
    jalr t0
    mv sp,s0
    lw ra,0(sp)
    lw gp,4(sp)
    lw s0,8(sp)
    lw s1,12(sp)
    lw s2,16(sp)
    lw s3,20(sp)
    lw s4,24(sp)
    lw s5,28(sp)
    lw s6,32(sp)
    lw s7,36(sp)
    lw s8,40(sp)
    lw s9,44(sp)
    lw s10,48(sp)
    lw s11,52(sp)
    addi sp,sp,64
    ret

/* OSB1 entry: OpenSBI owns M-mode traps and will enter its S-mode payload. */
.global bios_os_enter
bios_os_enter:
    mv t0,a0
    csrci mstatus,8
    csrw mie,zero
    csrw 0xbc0,zero
    csrw 0x9c0,zero
    csrw satp,zero
    sfence.vma
    li a0,0
    li a1,0
    li a2,0
    jr t0
