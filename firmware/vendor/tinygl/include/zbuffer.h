#ifndef TGL_ZBUFFER_H
#define TGL_ZBUFFER_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define ZB_Z_BITS 16

typedef struct {
    int x, y, z;
    int s, t;
    int r, g, b;
    float sz, tz;
} ZBufferPoint;

typedef struct {
    int xsize, ysize;
    int linesize;       /* bytes per scanline of pbuf */
    uint16_t *zbuf;     /* 16-bit unsigned Z buffer */
    uint16_t *pbuf;     /* RGB565 pixel buffer */
    const uint16_t *current_texture;
    int tex_w, tex_h;
    int tex_w_mask, tex_h_mask;
    int tex_w_shift;
    uint8_t zbuf_owned;
    uint8_t pbuf_owned;
} ZBuffer;

ZBuffer *ZB_open(int xsize, int ysize, uint16_t *pbuf, uint16_t *zbuf);
void ZB_close(ZBuffer *zb);
void ZB_setTarget(ZBuffer *zb, uint16_t *pbuf);
void ZB_clear(ZBuffer *zb, int clear_z, int z, int clear_color, uint16_t color);
void ZB_setTexture(ZBuffer *zb, const uint16_t *tex, int w, int h);

void ZB_fillTriangleFlat(ZBuffer *zb, const ZBufferPoint *p0,
                         const ZBufferPoint *p1, const ZBufferPoint *p2);
void ZB_fillTriangleSmooth(ZBuffer *zb, const ZBufferPoint *p0,
                           const ZBufferPoint *p1, const ZBufferPoint *p2);
void ZB_fillTriangleMapping(ZBuffer *zb, const ZBufferPoint *p0,
                            const ZBufferPoint *p1, const ZBufferPoint *p2);
void ZB_fillTriangleMappingSmooth(ZBuffer *zb, const ZBufferPoint *p0,
                                  const ZBufferPoint *p1, const ZBufferPoint *p2);

#ifdef __cplusplus
}
#endif

#endif /* TGL_ZBUFFER_H */
