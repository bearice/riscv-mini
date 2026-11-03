/* M-mode F/D execution test. Result words are in uncached DLM for JTAG.
 * The payload ABI stays ilp32/no-FPU; F/D instructions are assembled locally
 * with `.option arch` and move data through memory only. */
#include <stdint.h>
#include "ae350_address_map.h"

#define FP_ON ".option push\n.option arch, +d\n"
#define FP_OFF "\n.option pop"

static uint64_t in[32], out[32];

static uint64_t op_d(const char which, uint64_t a, uint64_t b, uint64_t c) {
    uint64_t r;
    switch (which) {
    case '+': __asm__ volatile(FP_ON "fld ft0,0(%1)\nfld ft1,0(%2)\nfadd.d ft2,ft0,ft1\nfsd ft2,0(%0)" FP_OFF :: "r"(&r), "r"(&a), "r"(&b) : "memory"); break;
    case '*': __asm__ volatile(FP_ON "fld ft0,0(%1)\nfld ft1,0(%2)\nfmul.d ft2,ft0,ft1\nfsd ft2,0(%0)" FP_OFF :: "r"(&r), "r"(&a), "r"(&b) : "memory"); break;
    case '/': __asm__ volatile(FP_ON "fld ft0,0(%1)\nfld ft1,0(%2)\nfdiv.d ft2,ft0,ft1\nfsd ft2,0(%0)" FP_OFF :: "r"(&r), "r"(&a), "r"(&b) : "memory"); break;
    case 's': __asm__ volatile(FP_ON "fld ft0,0(%1)\nfsqrt.d ft2,ft0\nfsd ft2,0(%0)" FP_OFF :: "r"(&r), "r"(&a) : "memory"); break;
    default:  __asm__ volatile(FP_ON "fld ft0,0(%1)\nfld ft1,0(%2)\nfld ft3,0(%3)\nfmadd.d ft2,ft0,ft1,ft3\nfsd ft2,0(%0)" FP_OFF
                 :: "r"(&r), "r"(&a), "r"(&b), "r"(&c) : "memory"); break;
    }
    return r;
}

void main(void) {
    volatile uint32_t *r = (volatile uint32_t *)AE350_RESULTS_BASE;
    uint32_t misa, mstatus, fcsr, single, word, bad = 0;
    for (unsigned i = 0; i < 16; i++) r[i] = 0;
    r[0] = 0xa350f001u;
    __asm__ volatile("csrr %0,misa" : "=r"(misa)); r[1] = misa;
    if (!(misa & (1u << 5)) || !(misa & (1u << 3))) bad |= 1;
    __asm__ volatile("csrs mstatus,%0" :: "r"(0x6000u));
    r[5] = 1;
    __asm__ volatile(FP_ON "fscsr zero" FP_OFF);
    const uint64_t one = 0x3ff0000000000000ull, two = 0x4000000000000000ull,
                   three = 0x4008000000000000ull, a = 0x3ff8000000000000ull /* 1.5 */,
                   b = 0x4002000000000000ull /* 2.25 */;
    static const struct { char op; uint64_t x, y, z, want; } cases[] = {
        {'+', a, b, 0, 0x400e000000000000ull},          /* 3.75 */
        {'*', a, b, 0, 0x400b000000000000ull},          /* 3.375 */
        {'f', a, b, one, 0x4011800000000000ull},        /* 1.5*2.25+1 = 4.375 */
        {'s', two, 0, 0, 0x3ff6a09e667f3bcdull},        /* sqrt(2) */
        {'/', one, three, 0, 0x3fd5555555555555ull},    /* 1/3, inexact */
    };
    for (unsigned i = 0; i < sizeof cases / sizeof cases[0]; i++) {
        uint64_t got = op_d(cases[i].op, cases[i].x, cases[i].y, cases[i].z);
        if (got != cases[i].want) { bad |= 2u << i; r[6] = i; r[7] = (uint32_t)got; r[8] = (uint32_t)(got >> 32); }
    }
    __asm__ volatile(FP_ON "frcsr %0" FP_OFF : "=r"(fcsr)); r[3] = fcsr;
    if ((fcsr & 0x1f) != 0x01) bad |= 0x80;   /* only NX from 1/3 */
    r[5] = 2;
    /* Single precision and conversions: 1.5f + 2.25f = 3.75f; (int)3.75f = 3 (rtz). */
    __asm__ volatile(FP_ON "li t0,0x3fc00000\nfmv.w.x ft0,t0\nli t0,0x40100000\nfmv.w.x ft1,t0\n"
       "fadd.s ft2,ft0,ft1\nfmv.x.w %0,ft2\nfcvt.w.s %1,ft2,rtz" FP_OFF : "=r"(single), "=r"(word) :: "t0");
    r[9] = single; r[10] = word;
    if (single != 0x40700000u || word != 3) bad |= 0x100;
    r[5] = 3;
    /* All 32 F registers hold distinct 64-bit patterns. */
    for (unsigned i = 0; i < 32; i++) in[i] = 0x0123456789abcdefull ^ ((uint64_t)i * 0x1111111111111111ull);
    __asm__ volatile(FP_ON ".irp n,0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31\n"
       "fld f\\n,(8*\\n)(%0)\n.endr\n"
       ".irp n,0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31\n"
       "fsd f\\n,(8*\\n)(%1)\n.endr" FP_OFF :: "r"(in), "r"(out) : "memory");
    unsigned mismatches = 0;
    for (unsigned i = 0; i < 32; i++) mismatches += out[i] != in[i];
    r[11] = mismatches;
    if (mismatches) bad |= 0x200;
    __asm__ volatile("csrr %0,mstatus" : "=r"(mstatus)); r[2] = mstatus;
    if (((mstatus >> 13) & 3) != 3 || !(mstatus >> 31)) bad |= 0x400;   /* FS dirty, SD set */
    r[4] = bad;
    r[5] = 4;
    r[15] = bad ? 0xbad00001u : 0x600d0000u;
    for (;;) __asm__ volatile("wfi");
}
