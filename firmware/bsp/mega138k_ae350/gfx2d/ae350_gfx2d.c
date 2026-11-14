#include "ae350_gfx2d.h"
#include "ae350_dma.h"

#define R(off) (*(volatile uint32_t *)(uintptr_t)(AE350_GFX2D_BASE+(off)))
#define W(off,value) (R(off)=(value))
#define fence() __asm__ volatile("fence rw,rw" ::: "memory")

int ae350_gfx2d_probe(void) {
    /* Cached: the TTY path issues thousands of ops; two APB reads per
     * call would dominate. Device identity does not change at runtime. */
    static int cached = -1;
    if (cached < 0)
        cached = (R(0)==AE350_GFX2D_ID && R(4)==AE350_ABI_VERSION)
            ? AE350_GFX2D_OK : AE350_GFX2D_DEVICE;
    return cached;
}

unsigned ae350_gfx2d_caps(void) {
    if (ae350_gfx2d_probe()) return 0;
    return R(GFX2D_CAP);
}

/* Cache-clean only the lines the engine actually touches: per row of the
 * rectangle (a full-span clean of a sparse cell costs ~600 line ops).
 * Batched CSR writes with one fence: per-row sync calls would fence 16x. */
static int sync_rect(uint32_t addr, uint32_t stride, uint32_t row_bytes,
                     uint32_t rows) {
    uint64_t span = (uint64_t)stride * (rows - 1) + row_bytes;
    if (span > AE350_DDR_SIZE || addr >= AE350_DDR_SIZE ||
        span > AE350_DDR_SIZE - addr)
        return AE350_GFX2D_ADDR;
    for (uint32_t r = 0; r < rows; r++) {
        uint32_t a = addr + r * stride;
        uint32_t last = (a + row_bytes - 1u) & ~(AE350_DMA_LINE - 1u);
        for (uint32_t line = a & ~(AE350_DMA_LINE - 1u); line <= last; line += AE350_DMA_LINE) {
            __asm__ volatile("csrw 0x7cb, %0" :: "r"(line) : "memory");
            __asm__ volatile("csrw 0x7cc, %0" :: "r"(1u) : "memory");
            if (line == last) break;
        }
    }
    fence();
    return AE350_GFX2D_OK;
}

/* Descriptor shadow: the engine latches all descriptor registers at
 * START, so rewriting an unchanged register is wasted APB traffic
 * (50 MHz fabric vs the CPU). The TTY path issues thousands of
 * same-shape ops; diffing cuts APB writes per op from 10 to ~4. */
struct gfx2d_desc {
    uint32_t op, src, dst, src_stride, dst_stride, w, h, fg, bg, wb, fmt;
};
static struct gfx2d_desc last_desc;
static int desc_valid;
static unsigned default_fmt = AE350_GFX2D_FMT_RGB565;

void ae350_gfx2d_reset_cache(void) {
    desc_valid = 0;
}

int ae350_gfx2d_set_format(unsigned format) {
    if (format == AE350_GFX2D_FMT_RGB888 || format > AE350_GFX2D_FMT_ARGB1555)
        return AE350_GFX2D_PARAM;
    default_fmt = format;
    return AE350_GFX2D_OK;
}

unsigned ae350_gfx2d_get_format(void) {
    return default_fmt;
}

static inline unsigned bpp_for_format(unsigned fmt) {
    switch (fmt) {
        case AE350_GFX2D_FMT_XRGB8888:
        case AE350_GFX2D_FMT_ARGB8888: return 4;
        case AE350_GFX2D_FMT_RGB888: return 3;
        case AE350_GFX2D_FMT_INDEX8: return 1;
        default: return 2;
    }
}

static void write_desc_diff(const struct gfx2d_desc *d) {
    if (!desc_valid || d->op != last_desc.op) W(GFX2D_OP, d->op);
    if (!desc_valid || d->src != last_desc.src) W(GFX2D_SRC_ADDR, d->src);
    if (!desc_valid || d->dst != last_desc.dst) W(GFX2D_DST_ADDR, d->dst);
    if (!desc_valid || d->src_stride != last_desc.src_stride) W(GFX2D_SRC_STRIDE, d->src_stride);
    if (!desc_valid || d->dst_stride != last_desc.dst_stride) W(GFX2D_DST_STRIDE, d->dst_stride);
    if (!desc_valid || d->w != last_desc.w) W(GFX2D_WIDTH, d->w);
    if (!desc_valid || d->h != last_desc.h) W(GFX2D_HEIGHT, d->h);
    if (!desc_valid || d->fg != last_desc.fg) W(GFX2D_FG_COLOR, d->fg);
    if (!desc_valid || d->bg != last_desc.bg) W(GFX2D_BG_COLOR, d->bg);
    if (!desc_valid || d->wb != last_desc.wb) W(GFX2D_BLIT_SRC_WB, d->wb);
    if (!desc_valid || d->fmt != last_desc.fmt) W(GFX2D_FORMAT, d->fmt);
    last_desc.op=d->op; last_desc.src=d->src; last_desc.dst=d->dst;
    last_desc.src_stride=d->src_stride; last_desc.dst_stride=d->dst_stride;
    last_desc.w=d->w; last_desc.h=d->h; last_desc.fg=d->fg; last_desc.bg=d->bg;
    last_desc.wb=d->wb; last_desc.fmt=d->fmt;
    desc_valid = 1;
    fence();
    W(GFX2D_CONTROL, 1);
    fence();
}

int ae350_gfx2d_fill_rect_fmt(uint32_t dst, uint32_t dst_stride,
                              uint32_t width, uint32_t height, uint32_t color,
                              unsigned format) {
    if (ae350_gfx2d_probe()) return AE350_GFX2D_DEVICE;
    if (!width || !height || format > AE350_GFX2D_FMT_XRGB1555)
        return AE350_GFX2D_PARAM;
    unsigned bpp = bpp_for_format(format);
    uint64_t bytes = (uint64_t)dst_stride * (height - 1) + (uint64_t)width * bpp;
    if (bytes > 0xffffffffull) return AE350_GFX2D_ADDR;
    if (sync_rect(dst, dst_stride, width * bpp, height)) return AE350_GFX2D_ADDR;
    write_desc_diff(&(struct gfx2d_desc){ .op=GFX2D_OP_FILL_RECT, .src=0, .dst=dst,
        .src_stride=0, .dst_stride=dst_stride, .w=width, .h=height,
        .fg=color, .bg=0, .wb=0, .fmt=format });
    return AE350_GFX2D_OK;
}

int ae350_gfx2d_fill_rect(uint32_t dst, uint32_t dst_stride,
                          uint32_t width, uint32_t height, uint16_t rgb565) {
    return ae350_gfx2d_fill_rect_fmt(dst, dst_stride, width, height, rgb565, default_fmt);
}

int ae350_gfx2d_copy_area_fmt(uint32_t src, uint32_t src_stride,
                              uint32_t dst, uint32_t dst_stride,
                              uint32_t width, uint32_t height,
                              unsigned format) {
    if (ae350_gfx2d_probe()) return AE350_GFX2D_DEVICE;
    if (!width || !height || format > AE350_GFX2D_FMT_XRGB1555)
        return AE350_GFX2D_PARAM;
    unsigned bpp = bpp_for_format(format);
    uint64_t row_bytes = (uint64_t)width * bpp;
    if (src < dst && (dst - src) < row_bytes) return AE350_GFX2D_PARAM;
    uint64_t src_span = (uint64_t)src_stride * (height - 1) + row_bytes;
    uint64_t dst_span = (uint64_t)dst_stride * (height - 1) + row_bytes;
    if (src_stride != dst_stride && (uint64_t)src < (uint64_t)dst + dst_span &&
        (uint64_t)dst < (uint64_t)src + src_span)
        return AE350_GFX2D_PARAM;
    if (dst_span > 0xffffffffull || src_span > 0xffffffffull) return AE350_GFX2D_ADDR;
    if (sync_rect(src, src_stride, (uint32_t)row_bytes, height) ||
        sync_rect(dst, dst_stride, (uint32_t)row_bytes, height))
        return AE350_GFX2D_ADDR;
    write_desc_diff(&(struct gfx2d_desc){ .op=GFX2D_OP_COPY_AREA, .src=src, .dst=dst,
        .src_stride=src_stride, .dst_stride=dst_stride, .w=width, .h=height,
        .fg=0, .bg=0, .wb=0, .fmt=format });
    return AE350_GFX2D_OK;
}

int ae350_gfx2d_copy_area(uint32_t src, uint32_t src_stride,
                          uint32_t dst, uint32_t dst_stride,
                          uint32_t width, uint32_t height) {
    return ae350_gfx2d_copy_area_fmt(src, src_stride, dst, dst_stride, width, height, default_fmt);
}

int ae350_gfx2d_image_blit_fmt(uint32_t src, uint32_t src_wb,
                               uint32_t dst, uint32_t dst_stride,
                               uint32_t width, uint32_t height,
                               uint32_t fg, uint32_t bg,
                               unsigned format) {
    if (ae350_gfx2d_probe()) return AE350_GFX2D_DEVICE;
    if (!width || !height || src_wb < (width + 7) / 8 ||
        format == AE350_GFX2D_FMT_RGB888 || format > AE350_GFX2D_FMT_XRGB1555)
        return AE350_GFX2D_PARAM;
    unsigned bpp = bpp_for_format(format);
    if (format == AE350_GFX2D_FMT_XRGB8888 && ((dst % 4) != 0 || (dst_stride % 4) != 0))
        return AE350_GFX2D_PARAM;
    if ((dst % 2) != 0 || (dst_stride % 2) != 0)
        return AE350_GFX2D_PARAM;
    uint64_t dbytes = (uint64_t)dst_stride * (height - 1) + (uint64_t)width * bpp;
    uint64_t sbytes = (uint64_t)src_wb * height;
    if (dbytes > 0xffffffffull || sbytes > 0xffffffffull) return AE350_GFX2D_ADDR;
    if (ae350_dma_sync_for_device(src, (uint32_t)sbytes) ||
        sync_rect(dst, dst_stride, (uint32_t)(width * bpp), height))
        return AE350_GFX2D_ADDR;
    write_desc_diff(&(struct gfx2d_desc){ .op=GFX2D_OP_IMAGE_BLIT, .src=src, .dst=dst,
        .src_stride=0, .dst_stride=dst_stride, .w=width, .h=height,
        .fg=fg, .bg=bg, .wb=src_wb, .fmt=format });
    return AE350_GFX2D_OK;
}

int ae350_gfx2d_image_blit(uint32_t src, uint32_t src_wb,
                           uint32_t dst, uint32_t dst_stride,
                           uint32_t width, uint32_t height,
                           uint16_t fg, uint16_t bg) {
    return ae350_gfx2d_image_blit_fmt(src, src_wb, dst, dst_stride, width, height, fg, bg, default_fmt);
}

#define SHADER_INST_HI(op, pred, dst, wr, s0, s1, s2, swz0) \
    (((uint32_t)(op) << 26) | ((uint32_t)(pred) << 24) | ((uint32_t)(dst) << 20) | \
     ((uint32_t)(wr) << 16) | ((uint32_t)(s0) << 12) | ((uint32_t)(s1) << 8) | \
     ((uint32_t)(s2) << 4) | ((uint32_t)(swz0) >> 4))
#define SHADER_INST_LO(swz0, swz1, swz2, flags, imm) \
    ((((uint32_t)(swz0) & 0xFu) << 28) | ((uint32_t)(swz1) << 20) | \
     ((uint32_t)(swz2) << 12) | ((uint32_t)(flags) << 8) | (uint32_t)(imm))

static int valid_shader_src_fmt(unsigned fmt) {
    return fmt != AE350_GFX2D_FMT_RGB888 && fmt < AE350_GFX2D_FMT_MONO1;
}

static int valid_shader_dst_fmt(unsigned fmt) {
    return fmt != AE350_GFX2D_FMT_RGB888 && fmt < AE350_GFX2D_FMT_INDEX8;
}

static int aligned_for_fmt(uint32_t addr, uint32_t stride, unsigned bpp) {
    if (bpp == 4) return (addr & 3u) == 0 && (stride & 3u) == 0;
    if (bpp == 2) return (addr & 1u) == 0 && (stride & 1u) == 0;
    return 1;
}

static void program_blend_shader(uint8_t alpha) {
    /* Stage 0: R0.a = MUL_UNORM8(SRC.a, U0.a); Stage 1: R0.rgb = LERP(SRC.rgb, DST.rgb, R0.a) */
    W(GFX2D_UNIFORM0, (uint32_t)alpha << 24);
    W(GFX2D_INST0_LO, SHADER_INST_LO(0xFFu, 0xFFu, 0xE4u, 0x8u, 0u));
    W(GFX2D_INST0_HI, SHADER_INST_HI(0x10u, 0u, 0u, 0x8u, 4u, 8u, 0u, 0xFFu));
    W(GFX2D_INST1_LO, SHADER_INST_LO(0xE4u, 0xE4u, 0xFFu, 0x8u, 0u));
    W(GFX2D_INST1_HI, SHADER_INST_HI(0x13u, 0u, 0u, 0x7u, 4u, 5u, 0u, 0xE4u));
}

int ae350_gfx2d_blend(uint32_t src, uint32_t src_stride, unsigned src_format,
                      uint32_t dst, uint32_t dst_stride, unsigned dst_format,
                      uint32_t width, uint32_t height,
                      uint8_t alpha, unsigned flags) {
    if (ae350_gfx2d_probe()) return AE350_GFX2D_DEVICE;
    if (!(R(GFX2D_CAP) & AE350_GFX2D_CAP_SHADER)) return AE350_GFX2D_UNSUPPORTED;
    if (!width || !height || width > 16384u || height > 16384u ||
        !valid_shader_src_fmt(src_format) || !valid_shader_dst_fmt(dst_format))
        return AE350_GFX2D_PARAM;
    unsigned sbpp = bpp_for_format(src_format);
    unsigned dbpp = bpp_for_format(dst_format);
    uint64_t srow = (uint64_t)width * sbpp;
    uint64_t drow = (uint64_t)width * dbpp;
    if (!aligned_for_fmt(src, src_stride, sbpp) || !aligned_for_fmt(dst, dst_stride, dbpp) ||
        src_stride < srow || dst_stride < drow)
        return AE350_GFX2D_PARAM;
    uint64_t sspan = (uint64_t)src_stride * (height - 1u) + srow;
    uint64_t dspan = (uint64_t)dst_stride * (height - 1u) + drow;
    if (sspan > 0xffffffffull || dspan > 0xffffffffull) return AE350_GFX2D_ADDR;
    if ((uint64_t)src < (uint64_t)dst + dspan && (uint64_t)dst < (uint64_t)src + sspan) {
        if (src_format != dst_format || src_stride != dst_stride ||
            width > 1024u || src > dst)
            return AE350_GFX2D_PARAM;
    }
    if (sync_rect(src, src_stride, (uint32_t)srow, height) ||
        sync_rect(dst, dst_stride, (uint32_t)drow, height))
        return AE350_GFX2D_ADDR;
    uint32_t cfg = 1u | (1u << 4) | (1u << 5) | (1u << 11) | (src_format << 12);
    if (flags & AE350_GFX2D_FLAG_DITHER) cfg |= (1u << 7);
    W(GFX2D_SHADER_CFG, cfg);
    program_blend_shader(alpha);
    write_desc_diff(&(struct gfx2d_desc){ .op=GFX2D_OP_SHADER, .src=src, .dst=dst,
        .src_stride=src_stride, .dst_stride=dst_stride, .w=width, .h=height,
        .fg=0, .bg=0, .wb=0, .fmt=dst_format });
    return AE350_GFX2D_OK;
}

int ae350_gfx2d_scale(uint32_t src, uint32_t src_stride,
                      uint32_t src_width, uint32_t src_height, unsigned src_format,
                      uint32_t dst, uint32_t dst_stride,
                      uint32_t dst_width, uint32_t dst_height, unsigned dst_format,
                      uint32_t u0, uint32_t v0, uint32_t du_dx, uint32_t dv_dy,
                      uint8_t alpha, unsigned flags) {
    if (ae350_gfx2d_probe()) return AE350_GFX2D_DEVICE;
    if (!(R(GFX2D_CAP) & AE350_GFX2D_CAP_SCALE)) return AE350_GFX2D_UNSUPPORTED;
    if (!src_width || !src_height || src_width > 16384u || src_height > 16384u ||
        !dst_width || !dst_height || dst_width > 16384u || dst_height > 16384u ||
        !valid_shader_src_fmt(src_format) || !valid_shader_dst_fmt(dst_format))
        return AE350_GFX2D_PARAM;
    unsigned sbpp = bpp_for_format(src_format);
    unsigned dbpp = bpp_for_format(dst_format);
    uint64_t srow = (uint64_t)src_width * sbpp;
    uint64_t drow = (uint64_t)dst_width * dbpp;
    if (!aligned_for_fmt(src, src_stride, sbpp) || !aligned_for_fmt(dst, dst_stride, dbpp) ||
        src_stride < srow || dst_stride < drow)
        return AE350_GFX2D_PARAM;
    uint64_t sspan = (uint64_t)src_stride * (src_height - 1u) + srow;
    uint64_t dspan = (uint64_t)dst_stride * (dst_height - 1u) + drow;
    if (sspan > 0xffffffffull || dspan > 0xffffffffull) return AE350_GFX2D_ADDR;
    if ((uint64_t)src < (uint64_t)dst + dspan && (uint64_t)dst < (uint64_t)src + sspan)
        return AE350_GFX2D_PARAM;
    if (sync_rect(src, src_stride, (uint32_t)srow, src_height) ||
        sync_rect(dst, dst_stride, (uint32_t)drow, dst_height))
        return AE350_GFX2D_ADDR;
    int do_blend = (alpha < 255u) || (flags & AE350_GFX2D_FLAG_BLEND);
    uint32_t cfg = (do_blend ? 1u : 0u) | (1u << 4) | (1u << 8) |
                   (1u << 11) | (src_format << 12);
    if (do_blend) cfg |= (1u << 5);
    if (flags & AE350_GFX2D_FLAG_BILINEAR) cfg |= (1u << 6);
    if (flags & AE350_GFX2D_FLAG_DITHER) cfg |= (1u << 7);
    W(GFX2D_SHADER_CFG, cfg);
    W(GFX2D_SRC_WIDTH, src_width);
    W(GFX2D_SRC_HEIGHT, src_height);
    W(GFX2D_UV_START_U, u0);
    W(GFX2D_UV_START_V, v0);
    W(GFX2D_UV_DU_DX, du_dx);
    W(GFX2D_UV_DV_DY, dv_dy);
    if (do_blend) {
        program_blend_shader(alpha);
    } else {
        W(GFX2D_INST0_LO, SHADER_INST_LO(0xE4u, 0xE4u, 0xE4u, 0x8u, 0u));
        W(GFX2D_INST0_HI, SHADER_INST_HI(0x00u, 0u, 0u, 0xFu, 4u, 0u, 0u, 0xE4u));
    }
    write_desc_diff(&(struct gfx2d_desc){ .op=GFX2D_OP_SCALE_BLIT, .src=src, .dst=dst,
        .src_stride=src_stride, .dst_stride=dst_stride, .w=dst_width, .h=dst_height,
        .fg=0, .bg=0, .wb=0, .fmt=dst_format });
    return AE350_GFX2D_OK;
}

int ae350_gfx2d_status(unsigned *status) {
    if (!status) return AE350_GFX2D_PARAM;
    *status = R(GFX2D_STATUS);
    return AE350_GFX2D_OK;
}

int ae350_gfx2d_wait(unsigned max_rounds) {
    for (unsigned i = 0; i < max_rounds; i++) {
        unsigned status = R(GFX2D_STATUS);
        if (status & GFX2D_STATUS_DONE)
            return (status & GFX2D_STATUS_ERROR) ? (int)R(GFX2D_LAST_ERROR) : AE350_GFX2D_OK;
        if (!(status & GFX2D_STATUS_BUSY)) return AE350_GFX2D_PARAM;
        /* Back off between polls: with the L1 I-cache off (bootloader reset
         * state), a tight APB spin starves instruction fetch while the
         * engine hammers DDR and faults as an illegal instruction. The
         * BIOS enables caches, but payloads may not; keep polling gentle. */
        for (volatile unsigned d = 0; d < 24; d++) {}
    }
    return AE350_GFX2D_TIMEOUT;
}
