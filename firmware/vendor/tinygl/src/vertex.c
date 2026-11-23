#include "zgl.h"

static int gl_clipcode(float x, float y, float z, float w) {
    int code = 0;
    if (x < -w) code |= 1;
    if (x >  w) code |= 2;
    if (y < -w) code |= 4;
    if (y >  w) code |= 8;
    if (z < -w) code |= 16;
    if (z >  w) code |= 32;
    return code;
}

void gl_transform_to_viewport(GLContext *c, GLVertex *v) {
    float winv = (v->pc.v[3] != 0.0f) ? (1.0f / v->pc.v[3]) : 1.0f;
    float x = v->pc.v[0] * winv;
    float y = v->pc.v[1] * winv;
    float z = v->pc.v[2] * winv;

    v->zp.sx = x * c->viewport.xscale + c->viewport.xcenter;
    v->zp.sy = y * c->viewport.yscale + c->viewport.ycenter;
    v->zp.fz = z * c->viewport.zscale + c->viewport.zcenter;
    if (v->zp.fz < 0.0f) v->zp.fz = 0.0f;
    if (v->zp.fz > 65535.0f) v->zp.fz = 65535.0f;
    v->zp.w = v->pc.v[3];
    v->zp.u = v->tex_coord.v[0];
    v->zp.v = v->tex_coord.v[1];

    v->zp.x = (int)v->zp.sx;
    v->zp.y = (int)v->zp.sy;
    v->zp.z = (int)v->zp.fz;

    float cr = v->color.v[0];
    float cg = v->color.v[1];
    float cb = v->color.v[2];
    if (cr < 0.0f) cr = 0.0f; else if (cr > 1.0f) cr = 1.0f;
    if (cg < 0.0f) cg = 0.0f; else if (cg > 1.0f) cg = 1.0f;
    if (cb < 0.0f) cb = 0.0f; else if (cb > 1.0f) cb = 1.0f;
    v->zp.r = (int)(cr * 255.0f * 256.0f);
    v->zp.g = (int)(cg * 255.0f * 256.0f);
    v->zp.b = (int)(cb * 255.0f * 256.0f);

    if (c->texture_2d_enabled && c->zb && c->zb->current_texture) {
        v->zp.s = (int)(v->tex_coord.v[0] * (float)c->zb->tex_w * 65536.0f);
        v->zp.t = (int)(v->tex_coord.v[1] * (float)c->zb->tex_h * 65536.0f);
    } else {
        v->zp.s = 0;
        v->zp.t = 0;
    }
}

static void gl_vertex_transform(GLContext *c, GLVertex *v) {
    uint64_t t0 = tgl_rdcycle();

    if (c->matrix_model_projection_updated) {
        const M4 *mv = &c->matrix_stack[0][c->matrix_stack_ptr[0]];
        const M4 *pr = &c->matrix_stack[1][c->matrix_stack_ptr[1]];
        gl_M4_Mul(&c->matrix_model_projection, pr, mv);
        c->matrix_model_projection_updated = 0;
    }

    const M4 *mvp = &c->matrix_model_projection;
    gl_M4_MulV4(&v->pc, mvp, &v->coord);
    v->clip_code = gl_clipcode(v->pc.v[0], v->pc.v[1], v->pc.v[2], v->pc.v[3]);

    if (c->lighting_enabled) {
        const M4 *mv = &c->matrix_stack[0][c->matrix_stack_ptr[0]];
        gl_M4_MulV4(&v->ec, mv, &v->coord);
        gl_shade_vertex(c, v);
    }

    if (v->clip_code == 0) {
        gl_transform_to_viewport(c, v);
    }

    c->stats.cycles_vertex += tgl_rdcycle() - t0;
}

void glBegin(GLenum mode) {
    GLContext *c = glGetContext();
    c->in_begin = 1;
    c->begin_type = mode;
    c->vertex_cnt = 0;
}

void glEnd(void) {
    GLContext *c = glGetContext();
    c->in_begin = 0;
    c->vertex_cnt = 0;
}

void glNormal3f(GLfloat nx, GLfloat ny, GLfloat nz) {
    GLContext *c = glGetContext();
    c->current_normal.v[0] = nx;
    c->current_normal.v[1] = ny;
    c->current_normal.v[2] = nz;
}

void glNormal3fv(const GLfloat *v) {
    glNormal3f(v[0], v[1], v[2]);
}

void glColor3f(GLfloat red, GLfloat green, GLfloat blue) {
    glColor4f(red, green, blue, 1.0f);
}

void glColor4f(GLfloat red, GLfloat green, GLfloat blue, GLfloat alpha) {
    GLContext *c = glGetContext();
    c->current_color.v[0] = red;
    c->current_color.v[1] = green;
    c->current_color.v[2] = blue;
    c->current_color.v[3] = alpha;
}

void glColor3ub(GLubyte red, GLubyte green, GLubyte blue) {
    glColor4f((float)red * (1.0f / 255.0f),
              (float)green * (1.0f / 255.0f),
              (float)blue * (1.0f / 255.0f), 1.0f);
}

void glTexCoord2f(GLfloat s, GLfloat t) {
    GLContext *c = glGetContext();
    c->current_tex_coord.v[0] = s;
    c->current_tex_coord.v[1] = t;
    c->current_tex_coord.v[2] = 0.0f;
    c->current_tex_coord.v[3] = 1.0f;
}

void glVertex3f(GLfloat x, GLfloat y, GLfloat z) {
    GLContext *c = glGetContext();
    if (!c->in_begin) return;

    int n = c->vertex_cnt;
    GLVertex *v = &c->vertex[n];
    v->coord.v[0] = x;
    v->coord.v[1] = y;
    v->coord.v[2] = z;
    v->coord.v[3] = 1.0f;
    v->normal = c->current_normal;
    v->color = c->current_color;
    v->tex_coord = c->current_tex_coord;
    v->edge_flag = 1;

    gl_vertex_transform(c, v);
    n++;

    switch (c->begin_type) {
    case GL_TRIANGLES:
        if (n == 3) {
            gl_draw_triangle(c, &c->vertex[0], &c->vertex[1], &c->vertex[2]);
            n = 0;
        }
        break;
    case GL_QUADS:
        if (n == 4) {
            if (c->shade_model == GL_FLAT) {
                GLVertex v2_flat = c->vertex[2];
                v2_flat.color = c->vertex[3].color;
                v2_flat.zp.r = c->vertex[3].zp.r;
                v2_flat.zp.g = c->vertex[3].zp.g;
                v2_flat.zp.b = c->vertex[3].zp.b;
                gl_draw_triangle(c, &c->vertex[0], &c->vertex[1], &v2_flat);
                gl_draw_triangle(c, &c->vertex[0], &c->vertex[2], &c->vertex[3]);
            } else {
                gl_draw_triangle(c, &c->vertex[0], &c->vertex[1], &c->vertex[2]);
                gl_draw_triangle(c, &c->vertex[0], &c->vertex[2], &c->vertex[3]);
            }
            n = 0;
        }
        break;
    case GL_TRIANGLE_STRIP:
        if (n == 3) {
            gl_draw_triangle(c, &c->vertex[0], &c->vertex[1], &c->vertex[2]);
        } else if (n > 3) {
            if (n & 1) {
                gl_draw_triangle(c, &c->vertex[n - 3], &c->vertex[n - 2], &c->vertex[n - 1]);
            } else {
                gl_draw_triangle(c, &c->vertex[n - 2], &c->vertex[n - 3], &c->vertex[n - 1]);
            }
            if (n >= TGL_VERTEX_BUF_SIZE) {
                c->vertex[0] = c->vertex[n - 2];
                c->vertex[1] = c->vertex[n - 1];
                n = 2;
            }
        }
        break;
    case GL_TRIANGLE_FAN:
        if (n >= 3) {
            gl_draw_triangle(c, &c->vertex[0], &c->vertex[n - 2], &c->vertex[n - 1]);
            if (n >= TGL_VERTEX_BUF_SIZE) {
                c->vertex[1] = c->vertex[n - 1];
                n = 2;
            }
        }
        break;
    case GL_QUAD_STRIP:
        if (n == 4) {
            if (c->shade_model == GL_FLAT) {
                GLVertex v2_flat = c->vertex[2];
                v2_flat.color = c->vertex[3].color;
                v2_flat.zp.r = c->vertex[3].zp.r;
                v2_flat.zp.g = c->vertex[3].zp.g;
                v2_flat.zp.b = c->vertex[3].zp.b;
                gl_draw_triangle(c, &c->vertex[0], &c->vertex[1], &c->vertex[3]);
                gl_draw_triangle(c, &c->vertex[0], &c->vertex[3], &v2_flat);
            } else {
                gl_draw_triangle(c, &c->vertex[0], &c->vertex[1], &c->vertex[3]);
                gl_draw_triangle(c, &c->vertex[0], &c->vertex[3], &c->vertex[2]);
            }
            c->vertex[0] = c->vertex[2];
            c->vertex[1] = c->vertex[3];
            n = 2;
        }
        break;
    default:
        n = 0;
        break;
    }
    c->vertex_cnt = n;
}

void glVertex3fv(const GLfloat *v) {
    glVertex3f(v[0], v[1], v[2]);
}
