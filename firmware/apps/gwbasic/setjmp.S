/* setjmp.S - non-local exit for the GW-BASIC payload.
 *
 * bas_setjmp stores ra, sp, gp, tp and s0..s11 into the bas_jmp_buf the caller
 * points at with a0, and returns 0.  bas_longjmp restores them and returns
 * `value` (a1), forcing 1 when value is 0, exactly like C setjmp/longjmp.
 * The buffer is 16 words.  Kept in assembly so the assembler never has to size
 * a naked inline-asm body. */
	.text
	.globl	bas_setjmp
	.type	bas_setjmp,@function
bas_setjmp:
	sw	ra,0(a0)
	sw	sp,4(a0)
	sw	gp,8(a0)
	sw	tp,12(a0)
	sw	s0,16(a0)
	sw	s1,20(a0)
	sw	s2,24(a0)
	sw	s3,28(a0)
	sw	s4,32(a0)
	sw	s5,36(a0)
	sw	s6,40(a0)
	sw	s7,44(a0)
	sw	s8,48(a0)
	sw	s9,52(a0)
	sw	s10,56(a0)
	sw	s11,60(a0)
	li	a0,0
	ret
	.size	bas_setjmp,.-bas_setjmp

	.globl	bas_longjmp
	.type	bas_longjmp,@function
bas_longjmp:
	lw	ra,0(a0)
	lw	sp,4(a0)
	lw	gp,8(a0)
	lw	tp,12(a0)
	lw	s0,16(a0)
	lw	s1,20(a0)
	lw	s2,24(a0)
	lw	s3,28(a0)
	lw	s4,32(a0)
	lw	s5,36(a0)
	lw	s6,40(a0)
	lw	s7,44(a0)
	lw	s8,48(a0)
	lw	s9,52(a0)
	lw	s10,56(a0)
	lw	s11,60(a0)
	mv	a0,a1
	beqz	a0,1f
	ret
1:	li	a0,1
	ret
	.size	bas_longjmp,.-bas_longjmp
