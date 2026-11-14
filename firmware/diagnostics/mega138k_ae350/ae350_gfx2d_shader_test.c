/* GFX2D Shader Core On-Board Acceptance Suite (Step 6a/6b/6c).
 * Tests GFX2D CAP flags, legacy OP regression, ARGB8888->RGB565 blend,
 * color-key discard, Stage 1 DOT3 grayscale, downward row overlap, the
 * 4-stage INDEX8/PAL -> DOT3 -> LUT_FETCH -> LERP chain, a 4-stage
 * predicate chain, DOT3/LUT_FETCH stage placement rejection, and the
 * OP_SCALE_BLIT bilinear upscale, tile span counterexample, 48-bit UV
 * clamp, two-tile downscale and scale mode validation.
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
    ERRORS,
    LUT4_BAD,
    PRED_BAD,
    PLACE_RC,
    SCALE_UP_BAD,
    SPAN_BAD,
    WRAP_BAD,
    DOWN_BAD,
    SCALE_RC,
    SLOT_COUNT
};

#define DONE_STAGE 0xC3500030u
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

static void put8(uint32_t addr, uint8_t v) {
    *(volatile uint8_t *)(uintptr_t)addr = v;
}
static uint32_t px32(uint32_t addr) {
    return *(volatile uint32_t *)(uintptr_t)addr;
}

static int wait_gfx2d(void) {
    for (unsigned i = 0; i < 100000; i++) {
        uint32_t st = R(AE350_GFX2D_STATUS);
        if (st & 4u) return (int)R(AE350_GFX2D_LAST_ERROR);
        if (st & 2u) return 0;
    }
    return -1;
}

#define SWZ_ID 0xE4u
#define SWZ_AAAA 0xFFu
#define F_SAT 0x8u
#define F_IMM 0x4u
#define INST_HI(op, pred, dst, wr, s0, s1, s2, swz0) \
    (((uint32_t)(op) << 26) | ((uint32_t)(pred) << 24) | ((uint32_t)(dst) << 20) | \
     ((uint32_t)(wr) << 16) | ((uint32_t)(s0) << 12) | ((uint32_t)(s1) << 8) | \
     ((uint32_t)(s2) << 4) | ((uint32_t)(swz0) >> 4))
#define INST_LO(swz0, swz1, swz2, flags, imm) \
    ((((uint32_t)(swz0) & 0xFu) << 28) | ((uint32_t)(swz1) << 20) | \
     ((uint32_t)(swz2) << 12) | ((uint32_t)(flags) << 8) | (uint32_t)(imm))

static void load_insts(const uint32_t inst[8]) {
    W(AE350_GFX2D_INST0_LO, inst[0]); W(AE350_GFX2D_INST0_HI, inst[1]);
    W(AE350_GFX2D_INST1_LO, inst[2]); W(AE350_GFX2D_INST1_HI, inst[3]);
    W(AE350_GFX2D_INST2_LO, inst[4]); W(AE350_GFX2D_INST2_HI, inst[5]);
    W(AE350_GFX2D_INST3_LO, inst[6]); W(AE350_GFX2D_INST3_HI, inst[7]);
}

static uint32_t pal_entry(unsigned i) {
    return (((i * 37u + 11u) & 0xFFu) << 24) | (((i * 3u) & 0xFFu) << 16) |
           (((i * 5u + 17u) & 0xFFu) << 8) | (255u - i);
}
static uint32_t lut_entry(unsigned i) {
    unsigned r = 2u * i > 255u ? 255u : 2u * i;
    return ((i ^ 0x5Au) << 24) | (r << 16) | (((i * i) >> 8) << 8) | (255u - i);
}
static uint32_t lut_dst_px(unsigned r, unsigned c) {
    return (0x10000000u * ((c + r) & 0xFu)) | ((c * 0x10203u) & 0xFFFFFFu);
}
static unsigned lerp8(int s, int d, unsigned a) {
    int w = (int)(a + (a >> 7));
    return (unsigned)(((s - d) * w + (d << 8) + 128) >> 8);
}

#define OP_SCALE_BLIT 4u
#define CFG_USES_SRC (1u << 4)
#define CFG_BILINEAR (1u << 6)
#define CFG_SCALE    (1u << 8)
#define CFG_SRC_FMT(f) ((1u << 11) | ((uint32_t)(f) << 12))
#define FMT_RGB565   0u
#define FMT_ARGB8888 4u

static uint32_t texel(unsigned u, unsigned v) {
    return (((u * 37u + v * 101u + 13u) & 0xFFu) << 24) | (((u * 11u + 7u) & 0xFFu) << 16) |
           (((v * 29u + u * 3u) & 0xFFu) << 8) | ((u ^ (v << 3)) & 0xFFu);
}
static uint16_t texel565(unsigned u, unsigned v) {
    return (uint16_t)(u * 37u + v * 1009u + u * v);
}
static uint32_t argb_of_565(uint16_t p) {
    unsigned r = (p >> 11) & 31u, g = (p >> 5) & 63u, b = p & 31u;
    return 0xFF000000u | (((r << 3) | (r >> 2)) << 16) |
           (((g << 2) | (g >> 4)) << 8) | ((b << 3) | (b >> 2));
}

/* top = LERP(P10, P00, u), bot = LERP(P11, P01, u), out = LERP(bot, top, v) */
static uint32_t bilinear(uint32_t p00, uint32_t p10, uint32_t p01, uint32_t p11,
                         unsigned fu, unsigned fv) {
    uint32_t out = 0;
    for (unsigned sh = 0; sh < 32; sh += 8) {
        unsigned top = lerp8((int)((p10 >> sh) & 0xFFu), (int)((p00 >> sh) & 0xFFu), fu);
        unsigned bot = lerp8((int)((p11 >> sh) & 0xFFu), (int)((p01 >> sh) & 0xFFu), fu);
        out |= (uint32_t)lerp8((int)bot, (int)top, fv) << sh;
    }
    return out;
}

/* UV in 48-bit UQ32.16 with edge clamp, as the hardware samples it. */
static uint32_t scale_ref(uint32_t (*tex)(unsigned, unsigned), unsigned sw, unsigned sh,
                          uint32_t u0, uint32_t du, uint32_t v0, uint32_t dv, int bil,
                          unsigned x, unsigned y) {
    uint64_t U = (uint64_t)u0 + (uint64_t)x * du;
    uint64_t V = (uint64_t)v0 + (uint64_t)y * dv;
    uint64_t ui = U >> 16, vi = V >> 16;
    unsigned cu0 = ui < sw - 1 ? (unsigned)ui : sw - 1;
    unsigned cv0 = vi < sh - 1 ? (unsigned)vi : sh - 1;
    if (!bil) return tex(cu0, cv0);
    unsigned cu1 = ui + 1 < sw - 1 ? (unsigned)ui + 1 : sw - 1;
    unsigned cv1 = vi + 1 < sh - 1 ? (unsigned)vi + 1 : sh - 1;
    return bilinear(tex(cu0, cv0), tex(cu1, cv0), tex(cu0, cv1), tex(cu1, cv1),
                    (unsigned)(U >> 8) & 0xFFu, (unsigned)(V >> 8) & 0xFFu);
}
static uint32_t tex565(unsigned u, unsigned v) { return argb_of_565(texel565(u, v)); }

/* Pass-through (MOV R0, SRC) scaled blit. */
static int run_scale(uint32_t op, uint32_t cfg, uint32_t src, uint32_t src_stride,
                     uint32_t sw, uint32_t sh, uint32_t dst, uint32_t dst_stride,
                     uint32_t w, uint32_t h, uint32_t fmt,
                     uint32_t u0, uint32_t du, uint32_t v0, uint32_t dv) {
    static const uint32_t inst[8] = {
        INST_LO(SWZ_ID, SWZ_ID, SWZ_ID, 0, 0), INST_HI(0x00, 0, 0, 0xF, 4, 0, 0, SWZ_ID),
    };
    W(AE350_GFX2D_OP, op);
    W(AE350_GFX2D_SHADER_CFG, cfg);
    W(AE350_GFX2D_SRC_ADDR, src);
    W(AE350_GFX2D_SRC_STRIDE, src_stride);
    W(AE350_GFX2D_SRC_WIDTH, sw);
    W(AE350_GFX2D_SRC_HEIGHT, sh);
    W(AE350_GFX2D_DST_ADDR, dst);
    W(AE350_GFX2D_DST_STRIDE, dst_stride);
    W(AE350_GFX2D_WIDTH, w);
    W(AE350_GFX2D_HEIGHT, h);
    W(AE350_GFX2D_FORMAT, fmt);
    W(AE350_GFX2D_UV_START_U, u0);
    W(AE350_GFX2D_UV_DU_DX, du);
    W(AE350_GFX2D_UV_START_V, v0);
    W(AE350_GFX2D_UV_DV_DY, dv);
    load_insts(inst);
    W(AE350_GFX2D_CONTROL, 1);
    return wait_gfx2d();
}

/* ARGB8888 texture -> ARGB8888 destination, compared against scale_ref. */
static unsigned scale_argb_case(uint32_t src, uint32_t dst, unsigned sw, unsigned sh,
                                unsigned w, unsigned h, uint32_t u0, uint32_t du,
                                uint32_t v0, uint32_t dv, int bil, int *rc) {
    uint32_t src_stride = sw * 4u, dst_stride = w * 4u;
    for (unsigned v = 0; v < sh; v++)
        for (unsigned u = 0; u < sw; u++) put32(src + v * src_stride + u * 4u, texel(u, v));
    for (unsigned i = 0; i < w * h; i++) put32(dst + i * 4u, 0);
    ae350_dma_sync_for_device(src, src_stride * sh);
    ae350_dma_sync_for_device(dst, dst_stride * h);
    *rc = run_scale(OP_SCALE_BLIT,
                    CFG_USES_SRC | CFG_SCALE | (bil ? CFG_BILINEAR : 0) | CFG_SRC_FMT(FMT_ARGB8888),
                    src, src_stride, sw, sh, dst, dst_stride, w, h, FMT_ARGB8888,
                    u0, du, v0, dv);
    if (*rc) return 0;
    ae350_dma_sync_for_cpu(dst, dst_stride * h);
    unsigned bad = 0;
    for (unsigned y = 0; y < h; y++)
        for (unsigned x = 0; x < w; x++)
            if (px32(dst + y * dst_stride + x * 4u) !=
                scale_ref(texel, sw, sh, u0, du, v0, dv, bil, x, y)) bad++;
    return bad;
}

int main(void) {
    for (unsigned i = 0; i < SLOT_COUNT; i++) slot[i] = 0;

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

        /* Stage 0: DISCARD_IF.rgb EQ, SRC.argb, U0.argb
         * Stage 1: MOV.rgb R0, SRC.argb (SAT=1)
         */
        uint32_t inst0_hi = (0x39u << 26) | (0u << 24) | (0u << 20) | (7u << 16) |
                            (4u << 12) | (8u << 8) | (0u << 4) | (0xE4u >> 4);
        uint32_t inst0_lo = (((0xE4u & 0xFu) << 28) | (0xE4u << 20) |
                            (0u << 12) | (0u << 8) | 0);
        uint32_t inst1_hi = (0x00u << 26) | (0u << 24) | (0u << 20) | (7u << 16) |
                            (4u << 12) | (0u << 8) | (0u << 4) | (0xE4u >> 4);
        uint32_t inst1_lo = (((0xE4u & 0xFu) << 28) | (0xE4u << 20) |
                            (0u << 12) | (0x8u << 8) | 0);
        uint32_t cfg = 1u | (1u << 4) | (1u << 11) | (2u << 12);

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
         * Stage 1: DOT3 R0 = R0 * UNIFORM0 ([B=29, G=150, R=77, A=0])
         */
        uint32_t inst0_hi = (0x00u << 26) | (0u << 24) | (0u << 20) |
                            (0xFu << 16) | (4u << 12) | (0u << 8) |
                            (0u << 4) | (0xE4u >> 4);
        uint32_t inst0_lo = (((0xE4u & 0xFu) << 28) | (0xE4u << 20) |
                            (0u << 12) | (0u << 8) | 0);
        uint32_t inst1_hi = (0x14u << 26) | (0u << 24) | (0u << 20) |
                            (7u << 16) | (0u << 12) | (8u << 8) |
                            (0u << 4) | (0xE4u >> 4);
        uint32_t inst1_lo = (((0xE4u & 0xFu) << 28) | (0xE4u << 20) |
                            (0u << 12) | (0x8u << 8) | 0);
        uint32_t cfg = 1u | (1u << 4) | (1u << 11) | (0u << 12);

        W(AE350_GFX2D_OP, 3);
        W(AE350_GFX2D_SHADER_CFG, cfg);
        W(AE350_GFX2D_UNIFORM0, (0u << 24) | (77u << 16) | (150u << 8) | 29u);
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

        /* Pure Red 0xF800 expands to R=255, G=0, B=0.
         * DOT3 = (255 * 77 + 128) >> 8 = 77 (0x4D).
         * Grayscale RGB565: R=9 (77>>3), G=19 (77>>2), B=9 (77>>3)
         * -> (9 << 11) | (19 << 5) | 9 = 0x4A69
         */
        uint16_t expected = (9u << 11) | (19u << 5) | 9u;
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

        uint32_t inst0_hi = (0x00u << 26) | (0u << 24) | (0u << 20) |
                            (7u << 16) | (4u << 12) | (0u << 8) |
                            (0u << 4) | (0xE4u >> 4);
        uint32_t inst0_lo = (((0xE4u & 0xFu) << 28) | (0xE4u << 20) |
                            (0u << 12) | (0x8u << 8) | 0);
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

    /* 8. Step 6b Example 3: INDEX8 -> PAL_RAM -> MOV -> DOT3 -> LUT_FETCH -> LERP */
    {
        uint32_t src = SCRATCH + 0xA003, dst = SCRATCH + 0xB000;
        uint32_t w = 40, h = 4, stride_src = 61, stride_dst = 256;
        for (unsigned r = 0; r < h; r++)
            for (unsigned c = 0; c < w; c++) {
                put8(src + r * stride_src + c, (uint8_t)((r * 7u + c * 13u) & 0xFFu));
                put32(dst + r * stride_dst + c * 4, lut_dst_px(r, c));
            }
        ae350_dma_sync_for_device(src & ~31u, stride_src * h + 32);
        ae350_dma_sync_for_device(dst, stride_dst * h);

        W(AE350_GFX2D_LUT_ADDR, 0x100u);
        for (unsigned i = 0; i < 256; i++) W(AE350_GFX2D_LUT_DATA, pal_entry(i));
        W(AE350_GFX2D_LUT_ADDR, 0u);
        for (unsigned i = 0; i < 256; i++) W(AE350_GFX2D_LUT_DATA, lut_entry(i));

        static const uint32_t inst[8] = {
            /* MOV.argb R0, SRC */
            INST_LO(SWZ_ID, SWZ_ID, SWZ_ID, 0, 0), INST_HI(0x00, 0, 0, 0xF, 4, 0, 0, SWZ_ID),
            /* DOT3.rgb R0, R0, U0 (SAT) */
            INST_LO(SWZ_ID, SWZ_ID, SWZ_ID, F_SAT, 0), INST_HI(0x14, 0, 0, 0x7, 0, 8, 0, SWZ_ID),
            /* LUT_FETCH.rgb R0, R0 */
            INST_LO(SWZ_ID, SWZ_ID, SWZ_ID, 0, 0), INST_HI(0x30, 0, 0, 0x7, 0, 0, 0, SWZ_ID),
            /* LERP.rgb R0, R0, DST, U1.aaaa (SAT) */
            INST_LO(SWZ_ID, SWZ_ID, SWZ_AAAA, F_SAT, 0), INST_HI(0x13, 0, 0, 0x7, 0, 5, 9, SWZ_ID),
        };
        W(AE350_GFX2D_OP, 3);
        W(AE350_GFX2D_SHADER_CFG, 3u | (1u << 4) | (1u << 5) | (1u << 11) | (6u << 12));
        W(AE350_GFX2D_UNIFORM0, 0x004D961Du);
        W(AE350_GFX2D_UNIFORM1, 0xC0C0C0C0u);
        W(AE350_GFX2D_SRC_ADDR, src);
        W(AE350_GFX2D_SRC_STRIDE, stride_src);
        W(AE350_GFX2D_DST_ADDR, dst);
        W(AE350_GFX2D_DST_STRIDE, stride_dst);
        W(AE350_GFX2D_WIDTH, w);
        W(AE350_GFX2D_HEIGHT, h);
        W(AE350_GFX2D_FORMAT, 4); /* ARGB8888 dst */
        load_insts(inst);
        W(AE350_GFX2D_CONTROL, 1);

        int rc = wait_gfx2d();
        if (rc) { slot[STAGE] = 0xC3500008u; slot[LUT4_BAD] = 0x80000000u | (uint32_t)rc; return 0; }
        ae350_dma_sync_for_cpu(dst, stride_dst * h);

        unsigned bad = 0;
        for (unsigned r = 0; r < h; r++)
            for (unsigned c = 0; c < w; c++) {
                uint32_t s = pal_entry((r * 7u + c * 13u) & 0xFFu);
                uint32_t d = lut_dst_px(r, c);
                unsigned sa = s >> 24, sr = (s >> 16) & 0xFFu;
                unsigned sg = (s >> 8) & 0xFFu, sb = s & 0xFFu;
                unsigned y = (sr * 77u + sg * 150u + sb * 29u + 128u) >> 8;
                if (y > 255u) y = 255u;
                uint32_t m = lut_entry(y);
                uint32_t want = (sa << 24) |
                    (lerp8((int)((m >> 16) & 0xFFu), (int)((d >> 16) & 0xFFu), 0xC0u) << 16) |
                    (lerp8((int)((m >> 8) & 0xFFu), (int)((d >> 8) & 0xFFu), 0xC0u) << 8) |
                    lerp8((int)(m & 0xFFu), (int)(d & 0xFFu), 0xC0u);
                if (px32(dst + r * stride_dst + c * 4) != want) bad++;
            }
        slot[LUT4_BAD] = bad;
        if (bad) { slot[STAGE] = 0xC3500008u; return 0; }
    }

    /* 9. Step 6b: predicate set in Stage 0 gates Stage 3 */
    {
        uint32_t src = SCRATCH + 0xC000, dst = SCRATCH + 0xC800;
        uint32_t w = 16, fg = 0x00123456u;
        for (unsigned c = 0; c < w; c++)
            put32(src + c * 4, (c << 16) | ((200u + c) << 8) | (c * 16u));
        for (unsigned c = 0; c < w; c++) put32(dst + c * 4, 0);
        ae350_dma_sync_for_device(src, 64);
        ae350_dma_sync_for_device(dst, 64);

        static const uint32_t inst[8] = {
            /* CMP_SET.b LT, SRC, U0 */
            INST_LO(SWZ_ID, SWZ_ID, SWZ_ID, 2, 0), INST_HI(0x38, 0, 0, 0x1, 4, 8, 0, SWZ_ID),
            /* ADD.rgb R0, SRC, #10 (SAT) */
            INST_LO(SWZ_ID, SWZ_ID, SWZ_ID, F_SAT | F_IMM, 10), INST_HI(0x01, 0, 0, 0x7, 4, 0, 0, SWZ_ID),
            /* SUB.rgb R0, R0, #3 (SAT) */
            INST_LO(SWZ_ID, SWZ_ID, SWZ_ID, F_SAT | F_IMM, 3), INST_HI(0x02, 0, 0, 0x7, 0, 0, 0, SWZ_ID),
            /* MOV.rgb R0, FG if P */
            INST_LO(SWZ_ID, SWZ_ID, SWZ_ID, 0, 0), INST_HI(0x00, 1, 0, 0x7, 6, 0, 0, SWZ_ID),
        };
        W(AE350_GFX2D_OP, 3);
        W(AE350_GFX2D_SHADER_CFG, 3u | (1u << 4) | (1u << 11) | (2u << 12));
        W(AE350_GFX2D_FG_COLOR, fg);
        W(AE350_GFX2D_UNIFORM0, 100u);
        W(AE350_GFX2D_SRC_ADDR, src);
        W(AE350_GFX2D_SRC_STRIDE, 64);
        W(AE350_GFX2D_DST_ADDR, dst);
        W(AE350_GFX2D_DST_STRIDE, 64);
        W(AE350_GFX2D_WIDTH, w);
        W(AE350_GFX2D_HEIGHT, 1);
        W(AE350_GFX2D_FORMAT, 2); /* XRGB8888 dst */
        load_insts(inst);
        W(AE350_GFX2D_CONTROL, 1);

        int rc = wait_gfx2d();
        if (rc) { slot[STAGE] = 0xC3500009u; slot[PRED_BAD] = 0x80000000u | (uint32_t)rc; return 0; }
        ae350_dma_sync_for_cpu(dst, 64);

        unsigned bad = 0;
        for (unsigned c = 0; c < w; c++) {
            uint32_t want;
            if (c * 16u < 100u) {
                want = 0xFF000000u | fg;
            } else {
                unsigned v[3] = { c, 200u + c, c * 16u };
                for (unsigned k = 0; k < 3; k++)
                    v[k] = (v[k] + 10u > 255u ? 255u : v[k] + 10u) - 3u;
                want = 0xFF000000u | (v[0] << 16) | (v[1] << 8) | v[2];
            }
            if (px32(dst + c * 4) != want) bad++;
        }
        slot[PRED_BAD] = bad;
        if (bad) { slot[STAGE] = 0xC3500009u; return 0; }
    }

    /* 10. Step 6b: LUT_FETCH outside Stage 2 is rejected without DDR writes */
    {
        uint32_t dst = SCRATCH + 0xD000;
        for (unsigned c = 0; c < 8; c++) put32(dst + c * 4, 0x5A5A5A5Au);
        ae350_dma_sync_for_device(dst, 32);
        static const uint32_t inst[8] = {
            INST_LO(SWZ_ID, SWZ_ID, SWZ_ID, 0, 0), INST_HI(0x30, 0, 0, 0xF, 6, 0, 0, SWZ_ID),
            INST_LO(SWZ_ID, SWZ_ID, SWZ_ID, 0, 0), INST_HI(0x00, 0, 0, 0xF, 6, 0, 0, SWZ_ID),
            INST_LO(SWZ_ID, SWZ_ID, SWZ_ID, 0, 0), INST_HI(0x00, 0, 0, 0xF, 6, 0, 0, SWZ_ID),
            INST_LO(SWZ_ID, SWZ_ID, SWZ_ID, 0, 0), INST_HI(0x00, 0, 0, 0xF, 6, 0, 0, SWZ_ID),
        };
        W(AE350_GFX2D_OP, 3);
        W(AE350_GFX2D_SHADER_CFG, 3u);
        W(AE350_GFX2D_DST_ADDR, dst);
        W(AE350_GFX2D_DST_STRIDE, 32);
        W(AE350_GFX2D_WIDTH, 8);
        W(AE350_GFX2D_HEIGHT, 1);
        W(AE350_GFX2D_FORMAT, 2);
        load_insts(inst);
        W(AE350_GFX2D_CONTROL, 1);

        int rc = wait_gfx2d();
        slot[PLACE_RC] = (uint32_t)rc;
        ae350_dma_sync_for_cpu(dst, 32);
        unsigned bad = 0;
        for (unsigned c = 0; c < 8; c++)
            if (px32(dst + c * 4) != 0x5A5A5A5Au) bad++;
        if (rc != AE350_GFX2D_PARAM || bad) { slot[STAGE] = 0xC350000Au; return 0; }
        W(AE350_GFX2D_CLEAR_ERRORS, 1);
    }

    /* 11. Step 6c: OP_SCALE_BLIT 2x upscale, nearest then bilinear */
    {
        int rc;
        unsigned bad = 0;
        for (int bil = 0; bil < 2; bil++) {
            bad += scale_argb_case(SCRATCH + 0x10000, SCRATCH + 0x11000, 8, 6, 16, 12,
                                   0, 0x8000u, 0, 0x8000u, bil, &rc);
            if (rc) { slot[STAGE] = 0xC350000Bu; slot[SCALE_UP_BAD] = 0x80000000u | (uint32_t)rc; return 0; }
        }
        slot[SCALE_UP_BAD] = bad;
        if (bad) { slot[STAGE] = 0xC350000Bu; return 0; }
    }

    /* 12. Step 6c: U_first=0xFFFF, dU=0x10001, bilinear over 1100 pixels
     * (the step choice must tile it as 1022 + 78 to keep spans <= 1024) */
    {
        int rc;
        unsigned bad = scale_argb_case(SCRATCH + 0x20000, SCRATCH + 0x24000, 1200, 2,
                                       1100, 1, 0xFFFFu, 0x10001u, 0, 0, 1, &rc);
        if (rc) { slot[STAGE] = 0xC350000Cu; slot[SPAN_BAD] = 0x80000000u | (uint32_t)rc; return 0; }
        slot[SPAN_BAD] = bad;
        if (bad) { slot[STAGE] = 0xC350000Cu; return 0; }
    }

    /* 13. Step 6c: U0/V0 = 0xFFFF0000 cross 2^32 and must clamp, not wrap */
    {
        int rc;
        unsigned bad = 0;
        for (int bil = 0; bil < 2; bil++) {
            bad += scale_argb_case(SCRATCH + 0x26000, SCRATCH + 0x26400, 8, 4, 4, 3,
                                   0xFFFF0000u, 0x10000u, 0xFFFF0000u, 0x10000u, bil, &rc);
            if (rc) { slot[STAGE] = 0xC350000Du; slot[WRAP_BAD] = 0x80000000u | (uint32_t)rc; return 0; }
        }
        slot[WRAP_BAD] = bad;
        if (bad) { slot[STAGE] = 0xC350000Du; return 0; }
    }

    /* 14. Step 6c: bilinear 2x downscale RGB565 -> RGB565 over 512 + 88 tiles */
    {
        uint32_t src = SCRATCH + 0x28000, dst = SCRATCH + 0x2C000;
        unsigned sw = 1210, sh = 4, w = 600, h = 2;
        uint32_t src_stride = 2432, dst_stride = 1280;
        uint32_t u0 = 0x8000u, du = 0x20000u, v0 = 0x4000u, dv = 0x18000u;
        for (unsigned v = 0; v < sh; v++)
            for (unsigned u = 0; u < sw; u++) put16(src + v * src_stride + u * 2u, texel565(u, v));
        for (unsigned y = 0; y < h; y++)
            for (unsigned x = 0; x < w; x++) put16(dst + y * dst_stride + x * 2u, 0);
        ae350_dma_sync_for_device(src, src_stride * sh);
        ae350_dma_sync_for_device(dst, dst_stride * h);
        int rc = run_scale(OP_SCALE_BLIT,
                           CFG_USES_SRC | CFG_SCALE | CFG_BILINEAR | CFG_SRC_FMT(FMT_RGB565),
                           src, src_stride, sw, sh, dst, dst_stride, w, h, FMT_RGB565,
                           u0, du, v0, dv);
        if (rc) { slot[STAGE] = 0xC350000Eu; slot[DOWN_BAD] = 0x80000000u | (uint32_t)rc; return 0; }
        ae350_dma_sync_for_cpu(dst, dst_stride * h);
        unsigned bad = 0;
        for (unsigned y = 0; y < h; y++)
            for (unsigned x = 0; x < w; x++) {
                uint32_t p = scale_ref(tex565, sw, sh, u0, du, v0, dv, 1, x, y);
                uint16_t want = (uint16_t)(((p >> 19) & 31u) << 11 |
                                           ((p >> 10) & 63u) << 5 | ((p >> 3) & 31u));
                if (px16(dst + y * dst_stride + x * 2u) != want) bad++;
            }
        slot[DOWN_BAD] = bad;
        if (bad) { slot[STAGE] = 0xC350000Eu; return 0; }
    }

    /* 15. Step 6c: OP_SCALE_BLIT without SCALE_EN is rejected without writes */
    {
        uint32_t dst = SCRATCH + 0x2E000;
        for (unsigned c = 0; c < 8; c++) put32(dst + c * 4, 0x5A5A5A5Au);
        ae350_dma_sync_for_device(dst, 32);
        int rc = run_scale(OP_SCALE_BLIT, CFG_USES_SRC | CFG_SRC_FMT(FMT_ARGB8888),
                           SCRATCH + 0x10000, 32, 8, 6, dst, 32, 8, 1, FMT_ARGB8888,
                           0, 0x10000u, 0, 0x10000u);
        slot[SCALE_RC] = (uint32_t)rc;
        ae350_dma_sync_for_cpu(dst, 32);
        unsigned bad = 0;
        for (unsigned c = 0; c < 8; c++)
            if (px32(dst + c * 4) != 0x5A5A5A5Au) bad++;
        if (rc != AE350_GFX2D_PARAM || bad) { slot[STAGE] = 0xC350000Fu; return 0; }
        W(AE350_GFX2D_CLEAR_ERRORS, 1);
    }

    slot[COMMANDS] = R(AE350_GFX2D_COMMANDS);
    slot[ERRORS] = R(AE350_GFX2D_ERRORS);
    slot[STAGE] = DONE_STAGE;

    __asm__ volatile("fence" ::: "memory");
    for (;;) __asm__ volatile("wfi");
    return 0;
}
