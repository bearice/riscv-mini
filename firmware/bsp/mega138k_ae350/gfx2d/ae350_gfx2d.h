#pragma once
#include <stdint.h>
#include "ae350_address_map.h"

/* GFX2D 2D pixel engine (see docs/platforms/mega138k_ae350/gfx2d.md).
 * Step 2: FILL_RECT only; other ops report AE350_GFX2D_UNSUPPORTED. */
enum {
    AE350_GFX2D_OK=0, AE350_GFX2D_UNSUPPORTED=1, AE350_GFX2D_BUSY=2,
    AE350_GFX2D_PARAM=3, AE350_GFX2D_ADDR=4, AE350_GFX2D_TIMEOUT=5,
    AE350_GFX2D_DEVICE=6, AE350_GFX2D_RANGE=7
};
enum {
    GFX2D_CONTROL=AE350_GFX2D_CONTROL, GFX2D_STATUS=AE350_GFX2D_STATUS,
    GFX2D_IRQ_STATUS=AE350_GFX2D_IRQ_STATUS, GFX2D_IRQ_ENABLE=AE350_GFX2D_IRQ_ENABLE,
    GFX2D_LAST_ERROR=AE350_GFX2D_LAST_ERROR, GFX2D_OP=AE350_GFX2D_OP,
    GFX2D_SRC_ADDR=AE350_GFX2D_SRC_ADDR, GFX2D_DST_ADDR=AE350_GFX2D_DST_ADDR,
    GFX2D_SRC_STRIDE=AE350_GFX2D_SRC_STRIDE, GFX2D_DST_STRIDE=AE350_GFX2D_DST_STRIDE,
    GFX2D_WIDTH=AE350_GFX2D_WIDTH, GFX2D_HEIGHT=AE350_GFX2D_HEIGHT,
    GFX2D_FG_COLOR=AE350_GFX2D_FG_COLOR, GFX2D_BG_COLOR=AE350_GFX2D_BG_COLOR,
    GFX2D_BLIT_SRC_WB=AE350_GFX2D_BLIT_SRC_WB, GFX2D_COMMANDS=AE350_GFX2D_COMMANDS,
    GFX2D_ERRORS=AE350_GFX2D_ERRORS, GFX2D_CLEAR_ERRORS=AE350_GFX2D_CLEAR_ERRORS,
    GFX2D_FORMAT=AE350_GFX2D_FORMAT
};
enum { GFX2D_OP_FILL_RECT=0, GFX2D_OP_COPY_AREA=1, GFX2D_OP_IMAGE_BLIT=2 };
enum {
    AE350_GFX2D_FMT_RGB565=0, AE350_GFX2D_FMT_RGB888=1,
    AE350_GFX2D_FMT_XRGB8888=2, AE350_GFX2D_FMT_XRGB1555=3
};
enum { GFX2D_STATUS_BUSY=1, GFX2D_STATUS_DONE=2, GFX2D_STATUS_ERROR=4 };

int ae350_gfx2d_probe(void);
int ae350_gfx2d_set_format(unsigned format);
unsigned ae350_gfx2d_get_format(void);
/* dst/stride are byte addresses. Color and row geometry follow format. */
int ae350_gfx2d_fill_rect_fmt(uint32_t dst, uint32_t dst_stride,
                              uint32_t width, uint32_t height, uint32_t color,
                              unsigned format);
int ae350_gfx2d_copy_area_fmt(uint32_t src, uint32_t src_stride,
                              uint32_t dst, uint32_t dst_stride,
                              uint32_t width, uint32_t height,
                              unsigned format);
int ae350_gfx2d_image_blit_fmt(uint32_t src, uint32_t src_wb,
                               uint32_t dst, uint32_t dst_stride,
                               uint32_t width, uint32_t height,
                               uint32_t fg, uint32_t bg,
                               unsigned format);
/* Convenience wrappers using current default format (RGB565). */
int ae350_gfx2d_fill_rect(uint32_t dst, uint32_t dst_stride,
                          uint32_t width, uint32_t height, uint16_t rgb565);
int ae350_gfx2d_copy_area(uint32_t src, uint32_t src_stride,
                          uint32_t dst, uint32_t dst_stride,
                          uint32_t width, uint32_t height);
int ae350_gfx2d_image_blit(uint32_t src, uint32_t src_wb,
                           uint32_t dst, uint32_t dst_stride,
                           uint32_t width, uint32_t height,
                           uint16_t fg, uint16_t bg);
int ae350_gfx2d_status(unsigned *status);
/* Bounded poll until DONE; returns AE350_GFX2D_OK or the engine error. */
int ae350_gfx2d_wait(unsigned max_rounds);
