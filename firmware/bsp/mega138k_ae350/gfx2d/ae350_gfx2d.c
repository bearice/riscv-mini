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
    uint32_t op, src, dst, src_stride, dst_stride, w, h, fg, bg, wb;
};
static struct gfx2d_desc last_desc;
static int desc_valid;

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
    last_desc.op=d->op; last_desc.src=d->src; last_desc.dst=d->dst;
    last_desc.src_stride=d->src_stride; last_desc.dst_stride=d->dst_stride;
    last_desc.w=d->w; last_desc.h=d->h; last_desc.fg=d->fg; last_desc.bg=d->bg;
    last_desc.wb=d->wb;
    desc_valid = 1;
    fence();
    W(GFX2D_CONTROL, 1);
    fence();
}

int ae350_gfx2d_fill_rect(uint32_t dst, uint32_t dst_stride,
                          uint32_t width, uint32_t height, uint16_t rgb565) {
    if (ae350_gfx2d_probe()) return AE350_GFX2D_DEVICE;
    if (!width || !height) return AE350_GFX2D_PARAM;
    /* The engine writes DDR directly: the rectangle's lines must be in
     * device-visible memory before START (cache clean, dma sync). */
    uint64_t bytes = (uint64_t)dst_stride * (height - 1) + (uint64_t)width * 2;
    if (bytes > 0xffffffffull) return AE350_GFX2D_ADDR;
    if (sync_rect(dst, dst_stride, width * 2, height)) return AE350_GFX2D_ADDR;
    write_desc_diff(&(struct gfx2d_desc){ .op=GFX2D_OP_FILL_RECT, .src=0, .dst=dst,
        .src_stride=0, .dst_stride=dst_stride, .w=width, .h=height,
        .fg=rgb565, .bg=0, .wb=0 });
    return AE350_GFX2D_OK;
}

int ae350_gfx2d_copy_area(uint32_t src, uint32_t src_stride,
                          uint32_t dst, uint32_t dst_stride,
                          uint32_t width, uint32_t height) {
    if (ae350_gfx2d_probe()) return AE350_GFX2D_DEVICE;
    if (!width || !height) return AE350_GFX2D_PARAM;
    uint64_t row_bytes = (uint64_t)width * 2;
    if (src < dst && (dst - src) < row_bytes) return AE350_GFX2D_PARAM;
    uint64_t src_span = (uint64_t)src_stride * (height - 1) + row_bytes;
    uint64_t dst_span = (uint64_t)dst_stride * (height - 1) + row_bytes;
    if (src_stride != dst_stride && (uint64_t)src < (uint64_t)dst + dst_span &&
        (uint64_t)dst < (uint64_t)src + src_span)
        return AE350_GFX2D_PARAM;
    if (dst_span > 0xffffffffull || src_span > 0xffffffffull) return AE350_GFX2D_ADDR;
    if (sync_rect(src, src_stride, width * 2, height) ||
        sync_rect(dst, dst_stride, width * 2, height))
        return AE350_GFX2D_ADDR;
    write_desc_diff(&(struct gfx2d_desc){ .op=GFX2D_OP_COPY_AREA, .src=src, .dst=dst,
        .src_stride=src_stride, .dst_stride=dst_stride, .w=width, .h=height,
        .fg=0, .bg=0, .wb=0 });
    return AE350_GFX2D_OK;
}

int ae350_gfx2d_image_blit(uint32_t src, uint32_t src_wb,
                           uint32_t dst, uint32_t dst_stride,
                           uint32_t width, uint32_t height,
                           uint16_t fg, uint16_t bg) {
    if (ae350_gfx2d_probe()) return AE350_GFX2D_DEVICE;
    if (!width || !height || src_wb < (width + 7) / 8) return AE350_GFX2D_PARAM;
    uint64_t dbytes = (uint64_t)dst_stride * (height - 1) + (uint64_t)width * 2;
    uint64_t sbytes = (uint64_t)src_wb * height;
    if (dbytes > 0xffffffffull || sbytes > 0xffffffffull) return AE350_GFX2D_ADDR;
    /* Source is one contiguous buffer (src_wb bytes per row): single sync.
     * Destination is strided: per-row sync of touched lines only. */
    if (ae350_dma_sync_for_device(src, (uint32_t)sbytes) ||
        sync_rect(dst, dst_stride, width * 2, height))
        return AE350_GFX2D_ADDR;
    write_desc_diff(&(struct gfx2d_desc){ .op=GFX2D_OP_IMAGE_BLIT, .src=src, .dst=dst,
        .src_stride=0, .dst_stride=dst_stride, .w=width, .h=height,
        .fg=fg, .bg=bg, .wb=src_wb });
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
