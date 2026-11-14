#include "zgl.h"

static GLContext g_ctx;

GLContext *glGetContext(void) {
    return &g_ctx;
}

void glInit(ZBuffer *zb) {
    GLContext *c = &g_ctx;
    uint8_t *p = (uint8_t *)c;
    for (unsigned i = 0; i < sizeof(*c); i++) p[i] = 0;

    c->zb = zb;
    for (int m = 0; m < 3; m++) {
        c->matrix_stack_ptr[m] = 0;
        gl_M4_Id(&c->matrix_stack[m][0]);
    }
    gl_M4_Id(&c->matrix_model_view_inv);
    gl_M4_Id(&c->matrix_model_projection);

    c->current_color.v[0] = 1.0f;
    c->current_color.v[1] = 1.0f;
    c->current_color.v[2] = 1.0f;
    c->current_color.v[3] = 1.0f;
    c->current_normal.v[2] = 1.0f;
    c->current_tex_coord.v[3] = 1.0f;

    c->cull_face = GL_BACK;
    c->front_face = GL_CCW;
    c->depth_write = 1;
    c->shade_model = GL_SMOOTH;
    c->tex_env_mode = GL_MODULATE;

    c->ambient_light_model.v[0] = 0.2f;
    c->ambient_light_model.v[1] = 0.2f;
    c->ambient_light_model.v[2] = 0.2f;
    c->ambient_light_model.v[3] = 1.0f;

    c->material.ambient.v[0] = 0.2f;
    c->material.ambient.v[1] = 0.2f;
    c->material.ambient.v[2] = 0.2f;
    c->material.ambient.v[3] = 1.0f;
    c->material.diffuse.v[0] = 0.8f;
    c->material.diffuse.v[1] = 0.8f;
    c->material.diffuse.v[2] = 0.8f;
    c->material.diffuse.v[3] = 1.0f;

    c->lights[0].diffuse.v[0] = 1.0f;
    c->lights[0].diffuse.v[1] = 1.0f;
    c->lights[0].diffuse.v[2] = 1.0f;
    c->lights[0].diffuse.v[3] = 1.0f;
    c->lights[0].position.v[2] = 1.0f;

    glViewport(0, 0, zb->xsize, zb->ysize);
}

void glClose(void) {
    (void)0;
}

void glSetClearHook(TGLClearHook hook, void *user) {
    GLContext *c = &g_ctx;
    c->clear_hook = hook;
    c->clear_hook_user = user;
}

void glResetStats(void) {
    GLContext *c = &g_ctx;
    c->stats.cycles_vertex = 0;
    c->stats.cycles_clip = 0;
    c->stats.cycles_raster = 0;
    c->stats.tris_submitted = 0;
    c->stats.tris_culled = 0;
    c->stats.tris_drawn = 0;
    c->stats.pixels_drawn = 0;
}

void glGetStats(TinyGLStats *out) {
    *out = g_ctx.stats;
}

void gl_eval_viewport(GLContext *c) {
    c->viewport.xscale = 0.5f * (float)c->viewport.xsize;
    c->viewport.yscale = -0.5f * (float)c->viewport.ysize;
    c->viewport.xcenter = (float)c->viewport.xmin + c->viewport.xscale;
    c->viewport.ycenter = (float)c->viewport.ymin + 0.5f * (float)c->viewport.ysize;
    c->viewport.zscale = 32767.0f;
    c->viewport.zcenter = 32767.5f;
}

void glViewport(GLint x, GLint y, GLsizei width, GLsizei height) {
    GLContext *c = &g_ctx;
    c->viewport.xmin = x;
    c->viewport.ymin = y;
    c->viewport.xsize = width;
    c->viewport.ysize = height;
    gl_eval_viewport(c);
}

void glClearColor(GLclampf red, GLclampf green, GLclampf blue, GLclampf alpha) {
    (void)alpha;
    GLContext *c = &g_ctx;
    int r = (int)(red * 31.0f);
    int g = (int)(green * 63.0f);
    int b = (int)(blue * 31.0f);
    if (r < 0) r = 0;
    if (r > 31) r = 31;
    if (g < 0) g = 0;
    if (g > 63) g = 63;
    if (b < 0) b = 0;
    if (b > 31) b = 31;
    c->clear_color_565 = (uint16_t)((r << 11) | (g << 5) | b);
}

void glClear(GLbitfield mask) {
    GLContext *c = &g_ctx;
    int cz = (mask & GL_DEPTH_BUFFER_BIT) != 0;
    int cc = (mask & GL_COLOR_BUFFER_BIT) != 0;
    if ((cc || cz) && c->clear_hook) {
        c->clear_hook(c->clear_hook_user, c->clear_color_565,
                      c->zb->xsize, c->zb->ysize,
                      cc ? c->zb->pbuf : (uint16_t *)0,
                      cz ? c->zb->zbuf : (uint16_t *)0);
        return;
    }
    if (cz || cc) {
        ZB_clear(c->zb, cz, 0xFFFF, cc, c->clear_color_565);
    }
}

static void set_cap(GLenum cap, int val) {
    GLContext *c = &g_ctx;
    switch (cap) {
    case GL_DEPTH_TEST:      c->depth_test = val; break;
    case GL_CULL_FACE:       c->cull_face_enabled = val; break;
    case GL_LIGHTING:        c->lighting_enabled = val; break;
    case GL_NORMALIZE:       c->normalize_enabled = val; break;
    case GL_TEXTURE_2D:      c->texture_2d_enabled = val; break;
    case GL_LIGHT0:
    case GL_LIGHT1:
    case GL_LIGHT2:
    case GL_LIGHT3:
        c->lights[cap - GL_LIGHT0].enabled = val;
        break;
    default: break;
    }
}

void glEnable(GLenum cap)  { set_cap(cap, 1); }
void glDisable(GLenum cap) { set_cap(cap, 0); }
void glCullFace(GLenum mode) { g_ctx.cull_face = mode; }
void glFrontFace(GLenum mode) { g_ctx.front_face = mode; }
void glDepthFunc(GLenum func) { (void)func; }
void glDepthMask(GLboolean flag) { g_ctx.depth_write = flag ? 1 : 0; }
void glShadeModel(GLenum mode) { g_ctx.shade_model = mode; }
void glFlush(void) {}
void glFinish(void) {}
