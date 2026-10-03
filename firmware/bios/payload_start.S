.section .text.start,"ax",@progbits
.global _payload_start
_payload_start:
    addi sp,sp,-16
    sw ra,0(sp)
    sw gp,4(sp)
    .option push
    .option norelax
    la gp,__global_pointer$
    .option pop
    call payload_main
    lw gp,4(sp)
    lw ra,0(sp)
    addi sp,sp,16
    ret
