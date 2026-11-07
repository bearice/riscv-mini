/* GFX2D board suite: FILL_RECT / COPY_AREA / IMAGE_BLIT pixel checks.
 *
 * Writes 32 result words at AE350_RESULTS_BASE; stage == 0xC3100005 means
 * all checks passed (0xC3100000 + small failure code otherwise).
 * Scratch lives in DIAG_VIDEO region (aligned diag scratch).
 */
#include <stdint.h>
#include "ae350_address_map.h"
#include "ae350_dma.h"
#include "ae350_gfx2d.h"

#define AT(offset) (AE350_DIAG_VIDEO_BASE + (offset))
#define SCRATCH AT(0x00020000u)
enum { STAGE, FILL_BAD, COPY_BAD, BLIT_BAD, OVLP_BAD, COMMANDS, ERRORS,
       FILL_CYCLES, COPY_CYCLES, BLIT_CYCLES };

#define DONE_STAGE 0xC3100005u
static volatile uint32_t *const slot = (volatile uint32_t *)AE350_RESULTS_BASE;

static uint32_t cycle(void) {
    uint32_t v;
    __asm__ volatile("csrr %0, mcycle" : "=r"(v));
    return v;
}
static uint16_t px(uint32_t addr) { return *(volatile uint16_t *)addr; }
static void put(uint32_t addr, uint16_t v) { *(volatile uint16_t *)addr = v; }

int main(void) {
    for (unsigned i = 0; i < 16; i++) slot[i] = 0;
    if (ae350_gfx2d_probe()) { slot[STAGE] = 0xC3100001u; return 0; }

    /* FILL: unaligned dst (odd), stride 100, 10x3 */
    {
        uint32_t dst = SCRATCH + 0x103, stride = 100, w = 10, h = 3;
        uint32_t t0 = cycle();
        ae350_gfx2d_fill_rect(dst, stride, w, h, 0xF800);
        int rc = ae350_gfx2d_wait(0xffff);
        slot[FILL_CYCLES] = cycle() - t0;
        if (rc) slot[STAGE] = 0xC3100002u;
        ae350_dma_sync_for_cpu(dst, stride * h);
        unsigned bad = 0;
        for (unsigned r = 0; r < h; r++)
            for (unsigned c = 0; c < w; c++)
                if (px(dst + r * stride + 2 * c) != 0xF800) bad++;
        slot[FILL_BAD] = bad;
    }
    /* COPY: src stride 64 -> dst stride 96, 10x3, then overlap-down */
    {
        uint32_t src = SCRATCH + 0x1000, dst = SCRATCH + 0x2000;
        for (unsigned r = 0; r < 3; r++)
            for (unsigned c = 0; c < 10; c++)
                put(src + r * 64 + 2 * c, (uint16_t)(0x1000 + r * 16 + c));
        ae350_dma_sync_for_device(src, 3 * 64);
        uint32_t t0 = cycle();
        ae350_gfx2d_copy_area(src, 64, dst, 96, 10, 3);
        int rc = ae350_gfx2d_wait(0xffff);
        slot[COPY_CYCLES] = cycle() - t0;
        if (rc) slot[STAGE] = 0xC3100003u;
        ae350_dma_sync_for_cpu(dst, 3 * 96);
        unsigned bad = 0;
        for (unsigned r = 0; r < 3; r++)
            for (unsigned c = 0; c < 10; c++)
                if (px(dst + r * 96 + 2 * c) != (uint16_t)(0x1000 + r * 16 + c)) bad++;
        slot[COPY_BAD] = bad;
        /* overlap: shift buffer contents down one row */
        uint32_t base = SCRATCH + 0x3000;
        for (unsigned r = 0; r < 4; r++)
            for (unsigned c = 0; c < 8; c++)
                put(base + r * 32 + 2 * c, (uint16_t)(0x2000 + r * 8 + c));
        ae350_dma_sync_for_device(base, 4 * 32);
        ae350_gfx2d_copy_area(base, 32, base + 32, 32, 8, 3);
        rc = ae350_gfx2d_wait(0xffff);
        if (rc) slot[STAGE] = 0xC3100004u;
        ae350_dma_sync_for_cpu(base, 4 * 32);
        unsigned obad = 0;
        for (unsigned r = 0; r < 3; r++)
            for (unsigned c = 0; c < 8; c++)
                if (px(base + 32 + r * 32 + 2 * c) != (uint16_t)(0x2000 + r * 8 + c)) obad++;
        slot[OVLP_BAD] = obad;
    }
    /* BLIT: 8x2 glyph, then 16x1 unaligned (offset 4) */
    {
        static const uint8_t glyph[2] = {0xA1, 0x5E};
        uint32_t src = SCRATCH + 0x5000, dst = SCRATCH + 0x6000;
        ((volatile uint8_t *)src)[0] = glyph[0];
        ((volatile uint8_t *)src)[1] = glyph[1];
        ae350_dma_sync_for_device(src, 2);
        uint32_t t0 = cycle();
        ae350_gfx2d_image_blit(src, 1, dst, 32, 8, 2, 0xF800, 0x001F);
        int rc = ae350_gfx2d_wait(0xffff);
        slot[BLIT_CYCLES] = cycle() - t0;
        if (rc) slot[STAGE] = 0xC3100006u;
        ae350_dma_sync_for_cpu(dst, 2 * 32);
        unsigned bad = 0;
        for (unsigned r = 0; r < 2; r++)
            for (unsigned c = 0; c < 8; c++) {
                uint16_t want = ((glyph[r] >> (7 - c)) & 1) ? 0xF800 : 0x001F;
                if (px(dst + r * 32 + 2 * c) != want) bad++;
            }
        slot[BLIT_BAD] = bad;  /* 8x2 count first, then add 16x1 below */
        /* 16x1 unaligned */
        ((volatile uint8_t *)(src + 16))[0] = 0xCA;
        ((volatile uint8_t *)(src + 16))[1] = 0x3C;
        ae350_dma_sync_for_device(src + 16, 2);
        uint32_t dst2 = SCRATCH + 0x7004;
        ae350_gfx2d_image_blit(src + 16, 2, dst2, 64, 16, 1, 0xF800, 0x001F);
        rc = ae350_gfx2d_wait(0xffff);
        if (rc) slot[STAGE] = 0xC3100007u;
        ae350_dma_sync_for_cpu(dst2, 64);
        const uint8_t g2[2] = {0xCA, 0x3C};
        unsigned bad2 = 0;
        for (unsigned c = 0; c < 16; c++) {
            uint16_t want = ((g2[c >> 3] >> (7 - (c & 7))) & 1) ? 0xF800 : 0x001F;
            if (px(dst2 + 2 * c) != want) bad2++;
        }
        slot[BLIT_BAD] = slot[BLIT_BAD] * 1000 + bad2;
    }
    slot[COMMANDS] = *(volatile uint32_t *)(AE350_GFX2D_BASE + GFX2D_COMMANDS);
    slot[ERRORS] = *(volatile uint32_t *)(AE350_GFX2D_BASE + GFX2D_ERRORS);
    if (!slot[STAGE] && !slot[FILL_BAD] && !slot[COPY_BAD] && !slot[BLIT_BAD] && !slot[OVLP_BAD])
        slot[STAGE] = DONE_STAGE;
    return 0;
}
