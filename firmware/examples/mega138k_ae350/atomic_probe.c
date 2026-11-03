/* Isolated A-extension test. Result words are in uncached DLM for JTAG.
 * Each build selects one memory/cache mode and one priming sequence; a hang
 * cannot count as PASS.
 *   AE350_ATOMIC_TARGET  0 = DLM, 1 = DDR with I/D cache on, 2 = DDR uncached
 *   AE350_ATOMIC_PRIME   bit 0: store 100 first, bit 1: load the word before the AMO,
 *                        bit 2: skip the AMO so LR/SC meets the primed line first */
#include <stdint.h>
#include "ae350_address_map.h"
#ifndef AE350_ATOMIC_TARGET
#define AE350_ATOMIC_TARGET 0
#endif
#ifndef AE350_ATOMIC_PRIME
#define AE350_ATOMIC_PRIME 1
#endif
/* DLM target: a word of dlm.results after the 16 result words. DDR target:
 * a cache line of this payload's .bss (contract region bios.bss). */
#define DLM_TARGET (AE350_RESULTS_BASE + 16u * 4u)
_Static_assert(DLM_TARGET + 4u <= AE350_RESULTS_BASE + AE350_RESULTS_BYTES, "DLM target escapes dlm.results");
static uint32_t ddr_line[8] __attribute__((aligned(32)));
/* Payloads are built without A; only these probe instructions use it. */
#define A_ON ".option push\n.option arch, +a\n"
#define A_OFF "\n.option pop"
void main(void) {
    volatile uint32_t *r = (volatile uint32_t *)AE350_RESULTS_BASE;
    volatile uint32_t *p = AE350_ATOMIC_TARGET == 0 ? (volatile uint32_t *)DLM_TARGET : ddr_line;
    uint32_t ctl, old, failed, loaded, initial = 0;
    __asm__ volatile("csrci mstatus,8\ncsrr %0,0x7ca" : "=r"(ctl) :: "memory");
    for (unsigned i=0;i<16;i++) r[i]=0;
    r[0]=0xa350a002u; r[1]=AE350_ATOMIC_TARGET | AE350_ATOMIC_PRIME << 8; r[2]=(uint32_t)p; r[3]=ctl;
    *p=0;
    __asm__ volatile("csrw 0x7cc,%0\nfence\ncsrc 0x7ca,%1\nfence.i" :: "r"(6u), "r"(3u) : "memory");
    if (AE350_ATOMIC_TARGET == 1) __asm__ volatile("csrs 0x7ca,%0\nfence.i" :: "r"(3u) : "memory");
    __asm__ volatile("csrr %0,0x7ca" : "=r"(ctl)); r[4]=ctl;
    if (AE350_ATOMIC_PRIME & 1) { *p=100; initial=100; __asm__ volatile("fence" ::: "memory"); }
    if (AE350_ATOMIC_PRIME & 2) r[11]=*p;
    /* Skipping the AMO must not read p: a load would allocate the line. */
    uint32_t added = AE350_ATOMIC_PRIME & 4 ? 0 : 7;
    r[5]=1;
    if (added) {
        __asm__ volatile(A_ON "amoadd.w.aqrl %0,%2,(%1)" A_OFF : "=r"(old) : "r"(p), "r"(added) : "memory");
        r[6]=old; r[7]=*p;
    }
    r[5]=2;
    unsigned attempts = 0;
    do {
        __asm__ volatile(A_ON "lr.w.aq %0,(%2)\naddi %0,%0,2\nsc.w.rl %1,%0,(%2)" A_OFF
                         : "=&r"(loaded), "=&r"(failed) : "r"(p) : "memory");
        r[12]=++attempts;
    } while (failed && attempts < 16);
    r[8]=failed; r[9]=*p; r[5]=3;
    r[10]=((!added || (old==initial && r[7]==initial+added)) && failed==0 && r[9]==initial+added+2)
          ? 0x600d0000u : 0xbad00001u;
    for (;;) __asm__ volatile("wfi");
}
