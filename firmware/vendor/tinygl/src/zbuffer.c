#include "zbuffer.h"

static ZBuffer g_zb;

ZBuffer *ZB_open(int xsize, int ysize, uint16_t *pbuf, uint16_t *zbuf) {
    ZBuffer *zb = &g_zb;
    zb->xsize = xsize;
    zb->ysize = ysize;
    zb->linesize = xsize * 2;
    zb->pbuf = pbuf;
    zb->zbuf = zbuf;
    zb->current_texture = (const uint16_t *)0;
    zb->tex_w = 0;
    zb->tex_h = 0;
    zb->tex_w_mask = 0;
    zb->tex_h_mask = 0;
    zb->tex_w_shift = 0;
    zb->pbuf_owned = 0;
    zb->zbuf_owned = 0;
    zb->depth_test = 1;
    zb->depth_write = 1;
    return zb;
}

void ZB_close(ZBuffer *zb) {
    (void)zb;
}

void ZB_setTarget(ZBuffer *zb, uint16_t *pbuf) {
    zb->pbuf = pbuf;
}

void ZB_setTexture(ZBuffer *zb, const uint16_t *tex, int w, int h) {
    zb->current_texture = tex;
    zb->tex_w = w;
    zb->tex_h = h;
    zb->tex_w_mask = (w > 0) ? (w - 1) : 0;
    zb->tex_h_mask = (h > 0) ? (h - 1) : 0;
    int shift = 0;
    int tmp = w;
    while (tmp > 1) {
        tmp >>= 1;
        shift++;
    }
    zb->tex_w_shift = shift;
}

void ZB_clear(ZBuffer *zb, int clear_z, int z, int clear_color, uint16_t color) {
    int count = zb->xsize * zb->ysize;
    if (clear_z && zb->zbuf) {
        uint32_t z32 = ((uint32_t)(uint16_t)z << 16) | (uint16_t)z;
        uint32_t *zp = (uint32_t *)zb->zbuf;
        int n32 = count >> 1;
        for (int i = 0; i < n32; i++) zp[i] = z32;
        if (count & 1) zb->zbuf[count - 1] = (uint16_t)z;
    }
    if (clear_color && zb->pbuf) {
        uint32_t c32 = ((uint32_t)color << 16) | color;
        uint32_t *pp = (uint32_t *)zb->pbuf;
        int n32 = count >> 1;
        for (int i = 0; i < n32; i++) pp[i] = c32;
        if (count & 1) zb->pbuf[count - 1] = color;
    }
}
