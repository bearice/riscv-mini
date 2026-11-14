#ifndef TGL_ZGL_H
#define TGL_ZGL_H

#include "GL/gl.h"
#include "zbuffer.h"

#define TGL_MAX_LIGHTS          4
#define TGL_MAX_TEXTURES        8
#define TGL_MAX_TEX_DIM         256
#define TGL_MATRIX_STACK_DEPTH  16
#define TGL_VERTEX_BUF_SIZE     16

typedef struct {
    float m[4][4];
} M4;

typedef struct {
    float v[3];
} V3;

typedef struct {
    float v[4];
} V4;

typedef struct {
    V4 ambient;
    V4 diffuse;
    V4 specular;
    V4 position;
    V3 norm_spot;
    int enabled;
} GLLight;

typedef struct {
    V4 ambient;
    V4 diffuse;
    V4 specular;
    V4 emission;
    float shininess;
} GLMaterial;

typedef struct {
    int width, height;
    int allocated;
    uint16_t data[TGL_MAX_TEX_DIM * TGL_MAX_TEX_DIM];
} GLTexture;

typedef struct {
    int edge_flag;
    V3 normal;
    V4 coord;
    V4 tex_coord;
    V4 color;
    V4 ec;      /* eye coordinates */
    V4 pc;      /* clip coordinates */
    int clip_code;
    ZBufferPoint zp;
} GLVertex;

typedef struct {
    uint64_t cycles_vertex;
    uint64_t cycles_clip;
    uint64_t cycles_raster;
    uint32_t tris_submitted;
    uint32_t tris_culled;
    uint32_t tris_drawn;
    uint32_t pixels_drawn;
} TinyGLStats;

typedef void (*TGLClearHook)(void *user, uint16_t color, int w, int h, uint16_t *pbuf, uint16_t *zbuf);

typedef struct {
    ZBuffer *zb;

    /* Matrix stacks */
    M4 matrix_stack[3][TGL_MATRIX_STACK_DEPTH];
    int matrix_stack_ptr[3];
    int matrix_mode;
    M4 matrix_model_view_inv;
    M4 matrix_model_projection;
    int matrix_model_projection_updated;
    int matrix_model_view_inv_updated;

    /* Viewport */
    struct {
        int xmin, ymin, xsize, ysize;
        float xcenter, ycenter;
        float xscale, yscale;
        float zcenter, zscale;
    } viewport;

    /* Current vertex attributes */
    V4 current_color;
    V3 current_normal;
    V4 current_tex_coord;

    /* Vertex assembly buffer */
    int in_begin;
    GLenum begin_type;
    int vertex_cnt;
    GLVertex vertex[TGL_VERTEX_BUF_SIZE];

    /* State */
    int depth_test;
    int depth_write;
    int cull_face_enabled;
    int cull_face;
    int front_face;
    int lighting_enabled;
    int normalize_enabled;
    int texture_2d_enabled;
    int shade_model;
    int tex_env_mode;

    uint16_t clear_color_565;

    /* Lighting */
    GLLight lights[TGL_MAX_LIGHTS];
    V4 ambient_light_model;
    GLMaterial material;

    /* Textures */
    GLTexture textures[TGL_MAX_TEXTURES];
    int current_tex_id;

    /* Optional hardware clear hook (e.g., GFX2D FILL_RECT) */
    TGLClearHook clear_hook;
    void *clear_hook_user;

    /* Profiling */
    TinyGLStats stats;
} GLContext;

/* Public TinyGL lifecycle & profiling helpers */
void glInit(ZBuffer *zb);
void glClose(void);
GLContext *glGetContext(void);
void glSetClearHook(TGLClearHook hook, void *user);
void glResetStats(void);
void glGetStats(TinyGLStats *out);

/* Cycle counter helper */
static inline uint64_t tgl_rdcycle(void) {
#if defined(__riscv)
    uint32_t hi0, lo, hi1;
    do {
        __asm__ volatile("csrr %0, 0xc80" : "=r"(hi0));
        __asm__ volatile("csrr %0, 0xc00" : "=r"(lo));
        __asm__ volatile("csrr %0, 0xc80" : "=r"(hi1));
    } while (hi0 != hi1);
    return ((uint64_t)hi1 << 32) | lo;
#else
    return 0;
#endif
}

/* Internal math */
void gl_M4_Id(M4 *a);
void gl_M4_Mul(M4 *c, const M4 *a, const M4 *b);
void gl_M4_MulLeft(M4 *c, const M4 *b);
void gl_M4_InvOrtho(M4 *a, const M4 *b);
void gl_M4_Inv(M4 *a, const M4 *b);
void gl_M4_Rotate(M4 *a, float t, int u);
void gl_M4_MulV4(V4 *a, const M4 *b, const V4 *c);
void gl_M3_MulV3(V3 *a, const M4 *b, const V3 *c);
int gl_V3_Norm(V3 *a);
float tgl_sinf(float x);
float tgl_cosf(float x);
float tgl_sqrtf(float x);

/* Internal pipeline */
void gl_eval_viewport(GLContext *c);
void gl_shade_vertex(GLContext *c, GLVertex *v);
void gl_draw_triangle(GLContext *c, GLVertex *p0, GLVertex *p1, GLVertex *p2);
void gl_transform_to_viewport(GLContext *c, GLVertex *v);

#endif /* TGL_ZGL_H */
