#include "zgl.h"

static void interpolate_vertex(GLVertex *out, const GLVertex *a, const GLVertex *b, float t) {
    float omt = 1.0f - t;
    for (int i = 0; i < 4; i++) {
        out->pc.v[i] = a->pc.v[i] * omt + b->pc.v[i] * t;
        out->color.v[i] = a->color.v[i] * omt + b->color.v[i] * t;
        out->tex_coord.v[i] = a->tex_coord.v[i] * omt + b->tex_coord.v[i] * t;
    }
    out->clip_code = 0;
}

static float plane_dist(const V4 *p, int plane) {
    switch (plane) {
    case 0: return p->v[3] + p->v[0]; /* x >= -w */
    case 1: return p->v[3] - p->v[0]; /* x <=  w */
    case 2: return p->v[3] + p->v[1]; /* y >= -w */
    case 3: return p->v[3] - p->v[1]; /* y <=  w */
    case 4: return p->v[3] + p->v[2]; /* z >= -w */
    case 5: return p->v[3] - p->v[2]; /* z <=  w */
    default: return 1.0f;
    }
}

static void emit_raster_triangle(GLContext *c, const GLVertex *p0, const GLVertex *p1, const GLVertex *p2) {
    /* Screen-space signed area for backface culling */
    int dx1 = p1->zp.x - p0->zp.x;
    int dy1 = p1->zp.y - p0->zp.y;
    int dx2 = p2->zp.x - p0->zp.x;
    int dy2 = p2->zp.y - p0->zp.y;
    /* Note: viewport yscale is negative, so screen-space +Y is down */
    int area = dx1 * dy2 - dy1 * dx2;
    if (area == 0) {
        c->stats.tris_culled++;
        return;
    }
    if (c->cull_face_enabled) {
        int front = (c->front_face == GL_CCW) ? (area < 0) : (area > 0);
        if ((c->cull_face == GL_BACK && !front) ||
            (c->cull_face == GL_FRONT && front) ||
            (c->cull_face == GL_FRONT_AND_BACK)) {
            c->stats.tris_culled++;
            return;
        }
    }

    uint64_t tr0 = tgl_rdcycle();
    c->stats.tris_drawn++;
    if (c->texture_2d_enabled && c->zb->current_texture) {
        if (c->shade_model == GL_SMOOTH) {
            ZB_fillTriangleMappingSmooth(c->zb, &p0->zp, &p1->zp, &p2->zp);
        } else {
            ZB_fillTriangleMapping(c->zb, &p0->zp, &p1->zp, &p2->zp);
        }
    } else if (c->shade_model == GL_SMOOTH) {
        ZB_fillTriangleSmooth(c->zb, &p0->zp, &p1->zp, &p2->zp);
    } else {
        ZB_fillTriangleFlat(c->zb, &p0->zp, &p1->zp, &p2->zp);
    }
    c->stats.cycles_raster += tgl_rdcycle() - tr0;
}

void gl_draw_triangle(GLContext *c, GLVertex *p0, GLVertex *p1, GLVertex *p2) {
    c->stats.tris_submitted++;
    int cc_and = p0->clip_code & p1->clip_code & p2->clip_code;
    if (cc_and != 0) {
        c->stats.tris_culled++;
        return;
    }
    int cc_or = p0->clip_code | p1->clip_code | p2->clip_code;
    if (cc_or == 0) {
        emit_raster_triangle(c, p0, p1, p2);
        return;
    }

    uint64_t tc0 = tgl_rdcycle();
    GLVertex buf_a[10], buf_b[10];
    GLVertex *in_v = buf_a, *out_v = buf_b;
    int in_n = 3;
    in_v[0] = *p0;
    in_v[1] = *p1;
    in_v[2] = *p2;

    for (int plane = 0; plane < 6; plane++) {
        int out_n = 0;
        if (in_n < 3) break;
        const GLVertex *prev = &in_v[in_n - 1];
        float prev_d = plane_dist(&prev->pc, plane);
        for (int i = 0; i < in_n; i++) {
            const GLVertex *curr = &in_v[i];
            float curr_d = plane_dist(&curr->pc, plane);
            if (curr_d >= 0.0f) {
                if (prev_d < 0.0f && out_n < 10) {
                    float t = prev_d / (prev_d - curr_d);
                    interpolate_vertex(&out_v[out_n++], prev, curr, t);
                }
                if (out_n < 10) out_v[out_n++] = *curr;
            } else if (prev_d >= 0.0f && out_n < 10) {
                float t = prev_d / (prev_d - curr_d);
                interpolate_vertex(&out_v[out_n++], prev, curr, t);
            }
            prev = curr;
            prev_d = curr_d;
        }
        GLVertex *tmp = in_v;
        in_v = out_v;
        out_v = tmp;
        in_n = out_n;
    }

    for (int i = 0; i < in_n; i++) {
        gl_transform_to_viewport(c, &in_v[i]);
    }
    c->stats.cycles_clip += tgl_rdcycle() - tc0;

    for (int i = 1; i + 1 < in_n; i++) {
        emit_raster_triangle(c, &in_v[0], &in_v[i], &in_v[i + 1]);
    }
}
