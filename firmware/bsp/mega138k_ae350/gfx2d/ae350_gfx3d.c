#include "ae350_gfx3d.h"
#include "ae350_gfx2d.h"

int ae350_gfx3d_wait(uint32_t timeout) {
    while (timeout--) {
        uint32_t st = AE350_G3D_STATUS;
        if (!(st & AE350_G3D_STATUS_BUSY)) {
            return (st & AE350_G3D_STATUS_ERR_PARAM) ? -2 : 0;
        }
    }
    return -1;
}

void ae350_gfx3d_init(void) {
    ae350_gfx3d_wait(0x100000u);
    /* Configure ShaderALU Stage 0: MUL_UNORM8(R0, SRC, R2) */
    AE350_GFX3D_REG(AE350_GFX2D_SHADER_CFG) = 0u;
    AE350_GFX3D_REG(AE350_GFX2D_INST0_LO)   = 0x4E4E4000u;
    AE350_GFX3D_REG(AE350_GFX2D_INST0_HI)   = 0x400F420Eu;
    AE350_G3D_BG_COLOR = 0xFF000000u;
}

void ae350_gfx3d_set_target(uint32_t fb_addr, uint32_t stride_px, uint32_t zbuf_addr) {
    ae350_gfx3d_wait(0x100000u);
    AE350_G3D_FB_ADDR   = fb_addr;
    AE350_G3D_FB_STRIDE = stride_px;
    AE350_G3D_ZBUF_ADDR = zbuf_addr;
}

static uint32_t ilog2_u32(int v) {
    uint32_t r = 0;
    while (v > 1) {
        v >>= 1;
        r++;
    }
    return r;
}

void ae350_gfx3d_upload_tex_rgb565(const uint16_t *tex, int w, int h,
                                   int bilinear, uint32_t wrap_u, uint32_t wrap_v) {
    ae350_gfx3d_wait(0x100000u);
    if (!tex || w <= 0 || h <= 0) {
        AE350_G3D_TEX_CFG = 0u;
        return;
    }
    uint32_t lw = ilog2_u32(w);
    uint32_t lh = ilog2_u32(h);
    AE350_G3D_TEX_CFG = AE350_G3D_TEX_EN |
                        (bilinear ? AE350_G3D_BILINEAR_EN : 0u) |
                        ((wrap_u & 3u) << 2) |
                        ((wrap_v & 3u) << 4) |
                        ((lw & 0xFu) << 7) |
                        ((lh & 0xFu) << 11);
    AE350_G3D_TMEM_ADDR = 0u;
    int count = w * h;
    for (int i = 0; i < count; i++) {
        uint16_t p = tex[i];
        uint32_t r5 = (p >> 11) & 0x1Fu;
        uint32_t g6 = (p >> 5) & 0x3Fu;
        uint32_t b5 = p & 0x1Fu;
        uint32_t r8 = (r5 << 3) | (r5 >> 2);
        uint32_t g8 = (g6 << 2) | (g6 >> 4);
        uint32_t b8 = (b5 << 3) | (b5 >> 2);
        AE350_G3D_TMEM_DATA = 0xFF000000u | (r8 << 16) | (g8 << 8) | b8;
    }
}
