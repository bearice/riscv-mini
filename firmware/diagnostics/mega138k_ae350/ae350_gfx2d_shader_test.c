/* GFX2D Shader Core On-Board Acceptance Suite (Step 6a).
 * Tests GFX2D CAP flags, legacy OP regression, ARGB8888->RGB565 blend,
 * color-key discard, Stage 1 DOT3 grayscale, and downward row overlap.
 */
#include <stdint.h>
#include "ae350_address_map.h"
#include "ae350_dma.h"
#include "ae350_gfx2d.h"

#define AT(offset) (AE350_DIAG_VIDEO_BASE + (offset))
#define SCRATCH    AT(0x00020000u)

enum {
    STAGE,
    PROBE_BAD,
    FILL_BAD,
    COPY_BAD,
    BLEND_BAD,
    DISCARD_BAD,
    DOT3_BAD,
    OVLP_BAD,
    COMMANDS,
    ERRORS
};

#define DONE_STAGE 0xC3500007u
static volatile uint32_t *const slot = (volatile uint32_t *)AE350_RESULTS_BASE;

#define R(off) (*(volatile uint32_t *)(uintptr_t)(AE350_GFX2D_BASE + (off)))
#define W(off, val) \
    (*(volatile uint32_t *)(uintptr_t)(AE350_GFX2D_BASE + (off)) = (uint32_t)(val))

static uint16_t px16(uint32_t addr) {
    return *(volatile uint16_t *)(uintptr_t)addr;
}
static void put16(uint32_t addr, uint16_t v) {
    *(volatile uint16_t *)(uintptr_t)addr = v;
}
static void put32(uint32_t addr, uint32_t v) {
    *(volatile uint32_t *)(uintptr_t)addr = v;
}

static int wait_gfx2d(void) {
    for (unsigned i = 0; i < 100000; i++) {
        uint32_t st = R(AE350_GFX2D_STATUS);
        if (st & 4u) return (int)R(AE350_GFX2D_LAST_ERROR);
        if (st & 2u) return 0;
    }
    return -1;
}

int main(void) {
    for (unsigned i = 0; i < 16; i++) slot[i] = 0;

    /* 1. Probe & CAP verification */
    uint32_t id = R(0);
    uint32_t abi = R(4);
    uint32_t cap = R(8);
    if (id != AE350_GFX2D_ID || abi != AE350_ABI_VERSION || !(cap & (1u << 7))) {
        slot[STAGE] = 0xC3500001u;
        slot[PROBE_BAD] = cap;
        return 0;
    }

    /* 2. Legacy OP 0 (FILL_RECT) regression */
    {
        uint32_t dst = SCRATCH + 0x100, stride = 64, w = 16, h = 4;
        ae350_gfx2d_fill_rect(dst, stride, w, h, 0xF800);
        int rc = ae350_gfx2d_wait(0xffff);
        if (rc) { slot[STAGE] = 0xC3500002u; return 0; }
        ae350_dma_sync_for_cpu(dst, stride * h);
        unsigned bad = 0;
        for (unsigned r = 0; r < h; r++)
            for (unsigned c = 0; c < w; c++)
                if (px16(dst + r * stride + 2 * c) != 0xF800) bad++;
        slot[FILL_BAD] = bad;
        if (bad) { slot[STAGE] = 0xC3500002u; return 0; }
    }

    /* 3. Legacy OP 1 (COPY_AREA) regression */
    {
        uint32_t src = SCRATCH + 0x1000, dst = SCRATCH + 0x2000;
        uint32_t stride = 64, w = 16, h = 4;
        for (unsigned r = 0; r < h; r++)
            for (unsigned c = 0; c < w; c++)
                put16(src + r * stride + 2 * c, (uint16_t)(0x1000 + r * 16 + c));
        ae350_dma_sync_for_device(src, stride * h);
        ae350_gfx2d_copy_area(src, stride, dst, stride, w, h);
        int rc = ae350_gfx2d_wait(0xffff);
        if (rc) { slot[STAGE] = 0xC3500003u; return 0; }
        ae350_dma_sync_for_cpu(dst, stride * h);
        unsigned bad = 0;
        for (unsigned r = 0; r < h; r++)
            for (unsigned c = 0; c < w; c++) {
                uint16_t want = (uint16_t)(0x1000 + r * 16 + c);
                if (px16(dst + r * stride + 2 * c) != want) bad++;
            }
        slot[COPY_BAD] = bad;
        if (bad) { slot[STAGE] = 0xC3500003u; return 0; }
    }

    /* 4. OP 3 (SHADER): Example 1 (Blend ARGB8888 -> RGB565) */
    {
        uint32_t src = SCRATCH + 0x3000, dst = SCRATCH + 0x4000;
        uint32_t w = 16, h = 2, stride_src = 64, stride_dst = 32;
        /* SRC: ARGB8888 (A=128, R=255, G=0, B=0) -> 0x80FF0000 */
        for (unsigned i = 0; i < w * h; i++)
            put32(src + i * 4, 0x80FF0000u);
        /* DST: RGB565 (Blue=31) -> 0x001F */
        for (unsigned i = 0; i < w * h; i++)
            put16(dst + i * 2, 0x001Fu);
        ae350_dma_sync_for_device(src, stride_src * h);
        ae350_dma_sync_for_device(dst, stride_dst * h);

        /* INST0: LERP DST, SRC, R0.aaaa
         * HI = (0x13 << 26) | (7 << 16) | (1 << 8) | (0xE4 >> 4) = 0x4c07010e
         * LO = ((0xE4 & 0xF) << 28) | (0xE4 << 20) | (0xFF << 12) | (0x8 << 8) | 0
         */
        uint32_t inst0_hi = ((0x13u << 26) | (7u << 16) | (1u << 8) | (0xE4u >> 4));
        uint32_t inst0_lo = (((0xE4u & 0xFu) << 28) | (0xE4u << 20) |
                             (0xFFu << 12) | (0x8u << 8) | 0);
        uint32_t cfg = (1u << 4) | (1u << 5) | (1u << 11) | (4u << 12);

        W(AE350_GFX2D_OP, 3);
        W(AE350_GFX2D_SHADER_CFG, cfg);
        W(AE350_GFX2D_SRC_ADDR, src);
        W(AE350_GFX2D_SRC_STRIDE, stride_src);
        W(AE350_GFX2D_DST_ADDR, dst);
        W(AE350_GFX2D_DST_STRIDE, stride_dst);
        W(AE350_GFX2D_WIDTH, w);
        W(AE350_GFX2D_HEIGHT, h);
        W(AE350_GFX2D_FORMAT, 0); /* RGB565 dst */
        W(AE350_GFX2D_INST0_LO, inst0_lo);
        W(AE350_GFX2D_INST0_HI, inst0_hi);
        W(AE350_GFX2D_CONTROL, 1);

        int rc = wait_gfx2d();
        if (rc) { slot[STAGE] = 0xC3500004u; return 0; }
        ae350_dma_sync_for_cpu(dst, stride_dst * h);

        uint16_t expected = (16u << 11) | 15u; /* Red 16, Blue 15 */
        unsigned bad = 0;
        for (unsigned i = 0; i < w * h; i++)
            if (px16(dst + i * 2) != expected) bad++;
        slot[BLEND_BAD] = bad;
        if (bad) { slot[STAGE] = 0xC3500004u; return 0; }
    }

    /* 5. OP 3 (SHADER): Example 2 (Color Key Discard) */
    {
        uint32_t src = SCRATCH + 0x5000, dst = SCRATCH + 0x6000;
        uint32_t w = 16, h = 1, stride_src = 64, stride_dst = 32;
        /* First 8 pixels: Key (Red 0x00FF0000), Next 8 pixels: Green (0x0000FF00) */
        for (unsigned i = 0; i < 8; i++) put32(src + i * 4, 0x00FF0000u);
        for (unsigned i = 8; i < 16; i++) put32(src + i * 4, 0x0000FF00u);
        /* Destination: Yellow (0xFFE0) */
        for (unsigned i = 0; i < 16; i++) put16(dst + i * 2, 0xFFE0u);
        ae350_dma_sync_for_device(src, stride_src);
        ae350_dma_sync_for_device(dst, stride_dst);

        /* Stage 0: DISCARD_IF SRC == R0 (key in UNIFORM0)
         * Stage 1: R0 = SRC
         */
        uint32_t inst0_hi = (0x17u << 26) | (2u << 24) | (7u << 16) |
                            (1u << 8) | (0xE4u >> 4);
        uint32_t inst0_lo = ((0xE4u & 0xFu) << 28) | (0xE4u << 20) |
                            (0x7u << 12) | (0x0u << 8) | 0;
        uint32_t inst1_hi = (0x00u << 26) | (0u << 24) | (7u << 16) |
                            (1u << 8) | (0xE4u >> 4);
        uint32_t inst1_lo = ((0xE4u & 0xFu) << 28) | (0xE4u << 20) |
                            (0xFu << 12) | (0x8u << 8) | 0;
        uint32_t cfg = 1u | (1u << 4) | (1u << 11) | (4u << 12);

        W(AE350_GFX2D_OP, 3);
        W(AE350_GFX2D_SHADER_CFG, cfg);
        W(AE350_GFX2D_UNIFORM0, 0x00FF0000u); /* Key: Red */
        W(AE350_GFX2D_SRC_ADDR, src);
        W(AE350_GFX2D_SRC_STRIDE, stride_src);
        W(AE350_GFX2D_DST_ADDR, dst);
        W(AE350_GFX2D_DST_STRIDE, stride_dst);
        W(AE350_GFX2D_WIDTH, w);
        W(AE350_GFX2D_HEIGHT, h);
        W(AE350_GFX2D_FORMAT, 0);
        W(AE350_GFX2D_INST0_LO, inst0_lo);
        W(AE350_GFX2D_INST0_HI, inst0_hi);
        W(AE350_GFX2D_INST1_LO, inst1_lo);
        W(AE350_GFX2D_INST1_HI, inst1_hi);
        W(AE350_GFX2D_CONTROL, 1);

        int rc = wait_gfx2d();
        if (rc) { slot[STAGE] = 0xC3500005u; return 0; }
        ae350_dma_sync_for_cpu(dst, stride_dst);

        unsigned bad = 0;
        /* First 8 pixels must be untouched Yellow (0xFFE0) */
        for (unsigned i = 0; i < 8; i++)
            if (px16(dst + i * 2) != 0xFFE0u) bad++;
        /* Last 8 pixels must be updated to Green (0x07E0) */
        for (unsigned i = 8; i < 16; i++)
            if (px16(dst + i * 2) != 0x07E0u) bad++;
        slot[DISCARD_BAD] = bad;
        if (bad) { slot[STAGE] = 0xC3500005u; return 0; }
    }

    /* 6. OP 3 (SHADER): Stage 1 DOT3 Grayscale Luminance */
    {
        uint32_t src = SCRATCH + 0x7000, dst = SCRATCH + 0x8000;
        uint32_t w = 16, h = 1, stride = 32;
        /* SRC: RGB565 pure Red (0xF800) */
        for (unsigned i = 0; i < w; i++) put16(src + i * 2, 0xF800u);
        ae350_dma_sync_for_device(src, stride);

        /* Stage 0: R0 = SRC
         * Stage 1: DOT3 R0 = R0 * UNIFORM0 ([76, 150, 29, 0])
         */
        uint32_t inst0_hi = (0x00u << 26) | (0u << 24) | (7u << 16) |
                            (1u << 8) | (0xE4u >> 4);
        uint32_t inst0_lo = ((0xE4u & 0xFu) << 28) | (0xE4u << 20) |
                            (0xFu << 12) | (0x8u << 8) | 0;
        uint32_t inst1_hi = (0x15u << 26) | (0u << 24) | (2u << 20) |
                            (7u << 16) | (0u << 12) | (2u << 8) |
                            (0u << 4) | (0xE4u >> 4);
        uint32_t inst1_lo = ((0xE4u & 0xFu) << 28) | (0xE4u << 20) |
                            (0x7u << 12) | (0x8u << 8) | 0;
        uint32_t cfg = 1u | (1u << 4) | (1u << 11) | (0u << 12);

        W(AE350_GFX2D_OP, 3);
        W(AE350_GFX2D_SHADER_CFG, cfg);
        W(AE350_GFX2D_UNIFORM0, 0x001D964Cu); /* [B=76, G=150, R=29, A=0] */
        W(AE350_GFX2D_SRC_ADDR, src);
        W(AE350_GFX2D_SRC_STRIDE, stride);
        W(AE350_GFX2D_DST_ADDR, dst);
        W(AE350_GFX2D_DST_STRIDE, stride);
        W(AE350_GFX2D_WIDTH, w);
        W(AE350_GFX2D_HEIGHT, h);
        W(AE350_GFX2D_FORMAT, 0);
        W(AE350_GFX2D_INST0_LO, inst0_lo);
        W(AE350_GFX2D_INST0_HI, inst0_hi);
        W(AE350_GFX2D_INST1_LO, inst1_lo);
        W(AE350_GFX2D_INST1_HI, inst1_hi);
        W(AE350_GFX2D_CONTROL, 1);

        int rc = wait_gfx2d();
        if (rc) { slot[STAGE] = 0xC3500006u; return 0; }
        ae350_dma_sync_for_cpu(dst, stride);

        /* 255 * 76 = 19380 -> 19380 / 256 = 75 (0x4B).
         * Grayscale RGB565: R=9 (75>>3), G=18 (75>>2), B=9 (75>>3)
         * -> (9 << 11) | (18 << 5) | 9 = 0x4A49
         */
        uint16_t expected = (9u << 11) | (18u << 5) | 9u;
        unsigned bad = 0;
        for (unsigned i = 0; i < w; i++)
            if (px16(dst + i * 2) != expected) bad++;
        slot[DOT3_BAD] = bad;
        if (bad) { slot[STAGE] = 0xC3500006u; return 0; }
    }

    /* 7. OP 3 (SHADER): Downward Overlap */
    {
        uint32_t base = SCRATCH + 0x9000;
        uint32_t stride = 64, w = 16, h = 3;
        for (unsigned r = 0; r < 4; r++)
            for (unsigned c = 0; c < w; c++)
                put16(base + r * stride + 2 * c, (uint16_t)(0x3000 + r * 16 + c));
        ae350_dma_sync_for_device(base, 4 * stride);

        uint32_t inst0_hi = (0x00u << 26) | (0u << 24) | (7u << 16) |
                            (1u << 8) | (0xE4u >> 4);
        uint32_t inst0_lo = ((0xE4u & 0xFu) << 28) | (0xE4u << 20) |
                            (0xFu << 12) | (0x8u << 8) | 0;
        uint32_t cfg = 0 | (1u << 4) | (1u << 11) | (0u << 12);

        W(AE350_GFX2D_OP, 3);
        W(AE350_GFX2D_SHADER_CFG, cfg);
        W(AE350_GFX2D_SRC_ADDR, base);
        W(AE350_GFX2D_SRC_STRIDE, stride);
        W(AE350_GFX2D_DST_ADDR, base + stride); /* Shift down 1 row */
        W(AE350_GFX2D_DST_STRIDE, stride);
        W(AE350_GFX2D_WIDTH, w);
        W(AE350_GFX2D_HEIGHT, h);
        W(AE350_GFX2D_FORMAT, 0);
        W(AE350_GFX2D_INST0_LO, inst0_lo);
        W(AE350_GFX2D_INST0_HI, inst0_hi);
        W(AE350_GFX2D_CONTROL, 1);

        int rc = wait_gfx2d();
        if (rc) { slot[STAGE] = 0xC3500007u; return 0; }
        ae350_dma_sync_for_cpu(base, 4 * stride);

        unsigned bad = 0;
        for (unsigned r = 0; r < h; r++)
            for (unsigned c = 0; c < w; c++) {
                uint16_t want = (uint16_t)(0x3000 + r * 16 + c);
                if (px16(base + stride + r * stride + 2 * c) != want) bad++;
            }
        slot[OVLP_BAD] = bad;
        if (bad) { slot[STAGE] = 0xC3500007u; return 0; }
    }

    slot[COMMANDS] = R(AE350_GFX2D_COMMANDS);
    slot[ERRORS] = R(AE350_GFX2D_ERRORS);
    slot[STAGE] = DONE_STAGE;

    __asm__ volatile("fence" ::: "memory");
    for (;;) __asm__ volatile("wfi");
    return 0;
}
