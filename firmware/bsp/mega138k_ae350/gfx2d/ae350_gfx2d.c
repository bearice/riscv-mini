#include "ae350_gfx2d.h"
#include "ae350_dma.h"

#define R(off) (*(volatile uint32_t *)(uintptr_t)(AE350_GFX2D_BASE+(off)))
#define W(off,value) (R(off)=(value))
#define fence() __asm__ volatile("fence rw,rw" ::: "memory")

int ae350_gfx2d_probe(void) {
    return (R(0)==AE350_GFX2D_ID && R(4)==AE350_ABI_VERSION)
        ? AE350_GFX2D_OK : AE350_GFX2D_DEVICE;
}

int ae350_gfx2d_fill_rect(uint32_t dst, uint32_t dst_stride,
                          uint32_t width, uint32_t height, uint16_t rgb565) {
    if (ae350_gfx2d_probe()) return AE350_GFX2D_DEVICE;
    if (!width || !height) return AE350_GFX2D_PARAM;
    /* The engine writes DDR directly: the rectangle's lines must be in
     * device-visible memory before START (cache clean, dma sync). */
    uint64_t bytes = (uint64_t)dst_stride * (height - 1) + (uint64_t)width * 2;
    if (bytes > 0xffffffffull) return AE350_GFX2D_ADDR;
    if (ae350_dma_sync_for_device(dst, (uint32_t)bytes)) return AE350_GFX2D_ADDR;
    W(GFX2D_OP, GFX2D_OP_FILL_RECT);
    W(GFX2D_SRC_ADDR, 0);
    W(GFX2D_DST_ADDR, dst);
    W(GFX2D_SRC_STRIDE, 0);
    W(GFX2D_DST_STRIDE, dst_stride);
    W(GFX2D_WIDTH, width);
    W(GFX2D_HEIGHT, height);
    W(GFX2D_FG_COLOR, rgb565);
    W(GFX2D_BG_COLOR, 0);
    W(GFX2D_BLIT_SRC_WB, 0);
    fence();
    W(GFX2D_CONTROL, 1);
    fence();
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
    }
    return AE350_GFX2D_TIMEOUT;
}
