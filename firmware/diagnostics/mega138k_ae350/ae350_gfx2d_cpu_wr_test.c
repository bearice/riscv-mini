/* CPU FB streaming write/readback test, cache on vs off. */
#include <stdint.h>
#include "ae350_address_map.h"
#include "ae350_dma.h"

static volatile uint32_t *out = (volatile uint32_t *)(AE350_RESULTS_BASE);

__attribute__((used)) void trap_report(unsigned cause, unsigned pc,
                                        unsigned value, unsigned ra, unsigned sp) {
    out[8] = 0x80000000u | cause; out[9] = pc; out[10] = value;
    out[11] = ra; out[12] = sp;
    for (;;) {}
}

static void flush_results(void) {
    ae350_dma_sync_for_device(AE350_RESULTS_BASE, 256u);
    __asm__ volatile("fence" ::: "memory");
}

static uint64_t rd_cycle(void) { uint64_t v; __asm__ volatile("csrr %0, 0xc00" : "=r"(v)); return v; }

#define N (640u * 480u)

static void test_pattern(unsigned slot, uint32_t *bad_out, uint32_t *first_out,
                         uint32_t *cyc_out) {
    uint16_t *fb = (uint16_t *)AE350_DIAG_VIDEO_FB0;
    uint64_t t0 = rd_cycle();
    for (unsigned i = 0; i < N; i++) fb[i] = (uint16_t)(i * 2654435761u >> 16);
    ae350_dma_sync_for_cpu(AE350_DIAG_VIDEO_FB0, 1280u * 480u);
    uint64_t t2 = rd_cycle();
    unsigned bad = 0; unsigned first = 0;
    for (unsigned i = 0; i < N; i++) {
        uint16_t want = (uint16_t)(i * 2654435761u >> 16);
        if (fb[i] != want) { if (!bad) first = i; bad++; }
    }
    (void)slot;
    *bad_out = bad; *first_out = first;
    *cyc_out = (uint32_t)(t2 - t0);
}

int main(void) {
    for (unsigned i = 0; i < 16; i++) out[i] = 0;
    out[0] = 0xC3600000u;
    __asm__ volatile("csrw 0x7cc,%0\ncsrs 0x7ca,%1" :: "r"(23), "r"(3) : "memory");
    test_pattern(1, (uint32_t *)&out[2], (uint32_t *)&out[4], (uint32_t *)&out[6]);
    out[0] = 0xC3600002u; flush_results();
    /* Phase 1: caches off (bootloader state) - small count only. */
    __asm__ volatile("csrw 0x7cc,%0\ncsrc 0x7ca,%1\n fence.i" :: "r"(23), "r"(3) : "memory");
    {
        uint16_t *fb = (uint16_t *)AE350_DIAG_VIDEO_FB0;
        for (unsigned i = 0; i < 4096u; i++) fb[i] = (uint16_t)(i * 2654435761u >> 16);
        unsigned bad = 0;
        for (unsigned i = 0; i < 4096u; i++)
            if (fb[i] != (uint16_t)(i * 2654435761u >> 16)) bad++;
        out[1] = bad;
    }
    out[0] = 0xC3600003u; flush_results();
    for (;;) __asm__ volatile("wfi");
}
