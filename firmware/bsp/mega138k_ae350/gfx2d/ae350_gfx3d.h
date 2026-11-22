#pragma once
#include <stdint.h>
#include "ae350_address_map.h"

#define AE350_GFX3D_REG(off) (*(volatile uint32_t *)(AE350_GFX2D_BASE + (off)))

#define AE350_G3D_TRI(i)      AE350_GFX3D_REG(0x100u + 4u * (i))
#define AE350_G3D_CTRL        AE350_GFX3D_REG(0x160u)
#define AE350_G3D_STATUS      AE350_GFX3D_REG(0x164u)
#define AE350_G3D_FB_ADDR     AE350_GFX3D_REG(0x168u)
#define AE350_G3D_FB_STRIDE   AE350_GFX3D_REG(0x16Cu)
#define AE350_G3D_TEX_CFG     AE350_GFX3D_REG(0x170u)
#define AE350_G3D_BG_COLOR    AE350_GFX3D_REG(0x174u)
#define AE350_G3D_Z_CFG       AE350_GFX3D_REG(0x178u)
#define AE350_G3D_ZBUF_ADDR   AE350_GFX3D_REG(0x17Cu)
#define AE350_G3D_TMEM_ADDR   AE350_GFX3D_REG(0x180u)
#define AE350_G3D_TMEM_DATA   AE350_GFX3D_REG(0x184u)
#define AE350_G3D_PIXELS_OUT  AE350_GFX3D_REG(0x188u)

#define AE350_G3D_STATUS_BUSY      (1u << 0)
#define AE350_G3D_STATUS_DONE      (1u << 1)
#define AE350_G3D_STATUS_ERR_PARAM (1u << 2)

#define AE350_G3D_FLAG_PERSP       (1u << 10)
#define AE350_G3D_FLAG_GOURAUD     (1u << 11)

#define AE350_G3D_TEX_EN           (1u << 0)
#define AE350_G3D_BILINEAR_EN      (1u << 1)
#define AE350_G3D_WRAP_REPEAT      0u
#define AE350_G3D_WRAP_CLAMP       1u
#define AE350_G3D_WRAP_BORDER      2u

#define AE350_G3D_Z_TEST_EN        (1u << 0)
#define AE350_G3D_Z_WRITE_EN       (1u << 1)
#define AE350_G3D_Z_FUNC_LESS      (1u << 2)
#define AE350_G3D_Z_FUNC_LEQUAL    (3u << 2)
#define AE350_G3D_Z_FUNC_ALWAYS    (7u << 2)
#define AE350_G3D_COLOR_WRITE_EN   (1u << 5)

void ae350_gfx3d_init(void);
int  ae350_gfx3d_wait(uint32_t timeout);
void ae350_gfx3d_set_target(uint32_t fb_addr, uint32_t stride_px, uint32_t zbuf_addr);
void ae350_gfx3d_upload_tex_rgb565(const uint16_t *tex, int w, int h,
                                   int bilinear, uint32_t wrap_u, uint32_t wrap_v);
