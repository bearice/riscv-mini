#include "zbuffer.h"

static inline uint16_t pack_rgb565(int r8, int g8, int b8) {
    return (uint16_t)(((r8 & 0xF8) << 8) | ((g8 & 0xFC) << 3) | ((b8 >> 3) & 0x1F));
}

static inline int mul_unorm8(int c, int u8) {
    int p = c * u8;
    return (p + 1 + (p >> 8)) >> 8;
}

static inline uint16_t modulate_rgb565(uint16_t tex, int r8, int g8, int b8) {
    int tr = (tex >> 11) & 0x1F;
    int tg = (tex >> 5)  & 0x3F;
    int tb = tex & 0x1F;
    int mr = mul_unorm8(tr, r8);
    int mg = mul_unorm8(tg, g8);
    int mb = mul_unorm8(tb, b8);
    return (uint16_t)((mr << 11) | (mg << 5) | mb);
}

#ifdef TGL_USE_ANDES_P
/* Andes P-extension ADD16 (rd = rs1 +_2x16 rs2, funct7=0x20, funct3=0, opcode=0x77) */
static inline uint32_t andes_add16(uint32_t a, uint32_t b) {
    uint32_t r;
    __asm__(".insn r 0x77, 0x0, 0x20, %0, %1, %2" : "=r"(r) : "r"(a), "r"(b));
    return r;
}
#endif

static void sort_vertices(const ZBufferPoint **v0, const ZBufferPoint **v1, const ZBufferPoint **v2) {
    const ZBufferPoint *tmp;
    if ((*v0)->y > (*v1)->y) { tmp = *v0; *v0 = *v1; *v1 = tmp; }
    if ((*v1)->y > (*v2)->y) { tmp = *v1; *v1 = *v2; *v2 = tmp; }
    if ((*v0)->y > (*v1)->y) { tmp = *v0; *v0 = *v1; *v1 = tmp; }
}

enum {
    MODE_FLAT = 0,
    MODE_SMOOTH = 1,
    MODE_TEX = 2,
    MODE_TEX_SMOOTH = 3
};

static void fill_triangle_impl(ZBuffer *zb, const ZBufferPoint *p0,
                               const ZBufferPoint *p1, const ZBufferPoint *p2,
                               int mode) {
    /* Capture provoking vertex (p2) flat color before y-sorting reorders vertices. */
    int flat_r8 = p2->r >> 8;
    int flat_g8 = p2->g >> 8;
    int flat_b8 = p2->b >> 8;
    uint16_t flat_color = pack_rgb565(flat_r8, flat_g8, flat_b8);
    int ztest = zb->depth_test;
    int zwrite = zb->depth_write;
    int tex_replace = zb->tex_env_replace;

    sort_vertices(&p0, &p1, &p2);

    int total_h = p2->y - p0->y;
    if (total_h <= 0) return;

    int xsize = zb->xsize;
    int ysize = zb->ysize;
    if (p2->y < 0 || p0->y >= ysize) return;

    /* Compute plane gradients via cross product using hardware FPU reciprocal */
    int dx1 = p1->x - p0->x;
    int dy1 = p1->y - p0->y;
    int dx2 = p2->x - p0->x;
    int dy2 = p2->y - p0->y;
    int fdx = dx1 * dy2 - dy1 * dx2;
    if (fdx == 0) return;

    float inv_area = 1.0f / (float)fdx;
    int64_t z_num = (int64_t)(p1->z - p0->z) * dy2 - (int64_t)(p2->z - p0->z) * dy1;
    int64_t dzdx = (z_num << 16) / fdx;
    int drdx = 0, dgdx = 0, dbdx = 0;
    int dsdx = 0, dtdx = 0;

    if (mode == MODE_SMOOTH || mode == MODE_TEX_SMOOTH) {
        drdx = (int)((float)((int64_t)(p1->r - p0->r) * dy2 - (int64_t)(p2->r - p0->r) * dy1) * inv_area);
        dgdx = (int)((float)((int64_t)(p1->g - p0->g) * dy2 - (int64_t)(p2->g - p0->g) * dy1) * inv_area);
        dbdx = (int)((float)((int64_t)(p1->b - p0->b) * dy2 - (int64_t)(p2->b - p0->b) * dy1) * inv_area);
    }
    if (mode == MODE_TEX || mode == MODE_TEX_SMOOTH) {
        dsdx = (int)((float)((int64_t)(p1->s - p0->s) * dy2 - (int64_t)(p2->s - p0->s) * dy1) * inv_area);
        dtdx = (int)((float)((int64_t)(p1->t - p0->t) * dy2 - (int64_t)(p2->t - p0->t) * dy1) * inv_area);
    }

    const uint16_t *tex = zb->current_texture;
    int tw_mask = zb->tex_w_mask;
    int th_mask = zb->tex_h_mask;
    int tw_shift = zb->tex_w_shift;

    /* Long edge p0 -> p2 (single FPU reciprocal) */
    float inv_th = 1.0f / (float)total_h;
    int32_t x_long = p0->x << 16;
    int32_t dx_long = (int32_t)((float)(p2->x - p0->x) * (65536.0f * inv_th));
    int64_t z_long = (int64_t)p0->z << 16;
    int64_t dz_long = ((int64_t)(p2->z - p0->z) << 16) / total_h;
    int32_t r_long = p0->r, g_long = p0->g, b_long = p0->b;
    int32_t dr_long = (int32_t)((float)(p2->r - p0->r) * inv_th);
    int32_t dg_long = (int32_t)((float)(p2->g - p0->g) * inv_th);
    int32_t db_long = (int32_t)((float)(p2->b - p0->b) * inv_th);
    int32_t s_long = p0->s, t_long = p0->t;
    int32_t ds_long = (int32_t)((float)(p2->s - p0->s) * inv_th);
    int32_t dt_long = (int32_t)((float)(p2->t - p0->t) * inv_th);

#ifdef TGL_USE_ANDES_P
    uint32_t dst_dx_packed = ((uint32_t)(uint16_t)(dsdx >> 8) << 16) | (uint16_t)(dtdx >> 8);
    uint32_t drdb_dx_packed = ((uint32_t)(uint16_t)drdx << 16) | (uint16_t)dbdx;
#endif

    for (int part = 0; part < 2; part++) {
        const ZBufferPoint *pa = (part == 0) ? p0 : p1;
        const ZBufferPoint *pb = (part == 0) ? p1 : p2;
        int seg_h = pb->y - pa->y;
        if (seg_h <= 0) continue;

        float inv_sh = 1.0f / (float)seg_h;
        int32_t x_short = pa->x << 16;
        int32_t dx_short = (int32_t)((float)(pb->x - pa->x) * (65536.0f * inv_sh));
        int64_t z_short = (int64_t)pa->z << 16;
        int64_t dz_short = ((int64_t)(pb->z - pa->z) << 16) / seg_h;
        int32_t r_short = pa->r, g_short = pa->g, b_short = pa->b;
        int32_t dr_short = (int32_t)((float)(pb->r - pa->r) * inv_sh);
        int32_t dg_short = (int32_t)((float)(pb->g - pa->g) * inv_sh);
        int32_t db_short = (int32_t)((float)(pb->b - pa->b) * inv_sh);
        int32_t s_short = pa->s, t_short = pa->t;
        int32_t ds_short = (int32_t)((float)(pb->s - pa->s) * inv_sh);
        int32_t dt_short = (int32_t)((float)(pb->t - pa->t) * inv_sh);

        int y_start = pa->y;
        int y_end = pb->y;

        for (int y = y_start; y < y_end; y++) {
            if (y >= 0 && y < ysize) {
                int32_t x_left, x_right;
                int64_t z;
                int32_t r, g, b, s, t;

                if (x_long <= x_short) {
                    x_left = x_long; x_right = x_short;
                    z = z_long; r = r_long; g = g_long; b = b_long; s = s_long; t = t_long;
                } else {
                    x_left = x_short; x_right = x_long;
                    z = z_short; r = r_short; g = g_short; b = b_short; s = s_short; t = t_short;
                }

                int xl = (int)((x_left + 0xFFFF) >> 16);
                int xr = (int)(((x_right + 0xFFFF) >> 16) - 1);
                if (xl < 0) xl = 0;
                if (xr >= xsize) xr = xsize - 1;

                if (xl <= xr) {
                    int64_t dx_sub = ((int64_t)xl << 16) - (int64_t)x_left;
                    z += (dzdx * dx_sub) >> 16;
                    r += (int32_t)(((int64_t)drdx * dx_sub) >> 16);
                    g += (int32_t)(((int64_t)dgdx * dx_sub) >> 16);
                    b += (int32_t)(((int64_t)dbdx * dx_sub) >> 16);
                    s += (int32_t)(((int64_t)dsdx * dx_sub) >> 16);
                    t += (int32_t)(((int64_t)dtdx * dx_sub) >> 16);

                    int row_off = y * xsize;
                    uint16_t *pp = zb->pbuf + row_off + xl;
                    uint16_t *zp = zb->zbuf + row_off + xl;
                    int len = xr - xl + 1;

                    if (mode == MODE_TEX_SMOOTH) {
                        uint16_t *zp_end = zp + len;
                        while (zp < zp_end) {
                            uint16_t zv = (uint16_t)(z >> 16);
                            if (!ztest || zv < *zp) {
                                if (zwrite) *zp = zv;
                                int tx = (s >> 16) & tw_mask;
                                int ty = (t >> 16) & th_mask;
                                uint16_t texel = tex[(ty << tw_shift) | tx];
                                *pp = tex_replace ? texel : modulate_rgb565(texel, r >> 8, g >> 8, b >> 8);
                            }
                            zp++;
                            pp++;
                            z += dzdx;
                            r += drdx;
                            g += dgdx;
                            b += dbdx;
                            s += dsdx;
                            t += dtdx;
                        }
                    } else if (mode == MODE_TEX) {
                        for (int i = 0; i < len; i++) {
                            uint16_t zv = (uint16_t)(z >> 16);
                            if (!ztest || zv < zp[i]) {
                                if (zwrite) zp[i] = zv;
                                int tx = (s >> 16) & tw_mask;
                                int ty = (t >> 16) & th_mask;
                                uint16_t texel = tex[(ty << tw_shift) | tx];
                                pp[i] = tex_replace ? texel : modulate_rgb565(texel, flat_r8, flat_g8, flat_b8);
                            }
                            z += dzdx;
                            s += dsdx;
                            t += dtdx;
                        }
                    } else if (mode == MODE_SMOOTH) {
                        for (int i = 0; i < len; i++) {
                            uint16_t zv = (uint16_t)(z >> 16);
                            if (!ztest || zv < zp[i]) {
                                if (zwrite) zp[i] = zv;
                                pp[i] = pack_rgb565(r >> 8, g >> 8, b >> 8);
                            }
                            z += dzdx;
                            r += drdx;
                            g += dgdx;
                            b += dbdx;
                        }
                    } else {
                        for (int i = 0; i < len; i++) {
                            uint16_t zv = (uint16_t)(z >> 16);
                            if (!ztest || zv < zp[i]) {
                                if (zwrite) zp[i] = zv;
                                pp[i] = flat_color;
                            }
                            z += dzdx;
                        }
                    }
                }
            }

            x_long += dx_long;   x_short += dx_short;
            z_long += dz_long;   z_short += dz_short;
            r_long += dr_long;   r_short += dr_short;
            g_long += dg_long;   g_short += dg_short;
            b_long += db_long;   b_short += db_short;
            s_long += ds_long;   s_short += ds_short;
            t_long += dt_long;   t_short += dt_short;
        }
    }
}

void ZB_fillTriangleFlat(ZBuffer *zb, const ZBufferPoint *p0,
                         const ZBufferPoint *p1, const ZBufferPoint *p2) {
    fill_triangle_impl(zb, p0, p1, p2, MODE_FLAT);
}

void ZB_fillTriangleSmooth(ZBuffer *zb, const ZBufferPoint *p0,
                           const ZBufferPoint *p1, const ZBufferPoint *p2) {
    fill_triangle_impl(zb, p0, p1, p2, MODE_SMOOTH);
}

void ZB_fillTriangleMapping(ZBuffer *zb, const ZBufferPoint *p0,
                            const ZBufferPoint *p1, const ZBufferPoint *p2) {
    fill_triangle_impl(zb, p0, p1, p2, MODE_TEX);
}

void ZB_fillTriangleMappingSmooth(ZBuffer *zb, const ZBufferPoint *p0,
                                  const ZBufferPoint *p1, const ZBufferPoint *p2) {
    fill_triangle_impl(zb, p0, p1, p2, MODE_TEX_SMOOTH);
}
