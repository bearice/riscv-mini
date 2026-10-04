.section .text
.globl _start
_start:
    li s0, 0x40000000
    li s1, 0x80000000
    li t0, 0x11223344
    sw t0, 0(s0)
    sw t0, 4(s0)
    sw t0, 8(s0)
    sw t0, 12(s0)
    fence rw, rw
    lw t1, 0(s0)
    bne t0, t1, fail
    li t2, 1
    sw t2, 0(s1)
    li t0, 0x55667788
    sw t0, 16(s0)
    fence.i
    li t2, 2
    sw t2, 0(s1)
    li t0, 100
    sw t0, 32(s0)
    li t1, 7
    amoadd.w.aqrl t2, t1, (s0)
    li t3, 0x11223344
    bne t2, t3, fail
    li t2, 3
    sw t2, 0(s1)
    lr.w.aq t0, (s0)
    li t3, 0x1122334b
    bne t0, t3, fail
    addi t1, t0, 2
    sc.w.rl t2, t1, (s0)
    bnez t2, fail
    fence rw, rw
    li s2, 0x40000080
    li t0, 0x00700513
    sw t0, 0(s2)
    li t0, 0x00008067
    sw t0, 4(s2)
    fence.i
    jalr ra, s2, 0
    li t0, 7
    bne a0, t0, fail
    li t0, 0x00900513
    sw t0, 0(s2)
    fence.i
    jalr ra, s2, 0
    li t0, 9
    bne a0, t0, fail
    li t2, 4
    sw t2, 0(s1)
1:  j 1b
fail:
    li t2, 255
    sw t2, 0(s1)
    j fail
