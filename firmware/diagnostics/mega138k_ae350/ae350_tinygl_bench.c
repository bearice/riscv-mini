/* AE350 TinyGL GFX3D Hardware-Accelerated Benchmark:
 *   Single-texture 1,000-triangle rotating 3D mesh, single-light Gouraud shading,
 *   GFX3D hardware perspective-correct rasterizer + 16-bit Z-buffer,
 *   GFX2D FILL_RECT clear & double-buffered XRGB8888 HDMI swap.
 *
 * Writes results to AE350_RESULTS_BASE (800 MHz CPU clock):
 *   out[0] = status (0xC3900000 running, 0xC3900001 done)
 *   out[1] = gfx2d_ok (1 if GFX2D probe succeeded)
 *   out[2] = 320x240 FPS (x100, e.g. 6000 = 60.00 FPS)
 *   out[3] = 320x240 avg cycles/frame (total)
 *   out[4] = 320x240 avg cycles/frame (vertex+clip)
 *   out[5] = 320x240 avg cycles/frame (raster/submit)
 *   out[6] = 640x480 FPS (x100)
 *   out[7] = 640x480 avg cycles/frame (total)
 *   out[8] = 640x480 avg cycles/frame (vertex+clip)
 *   out[9] = 640x480 avg cycles/frame (raster/submit)
 *   out[10] = tris_submitted (last frame)
 *   out[11] = tris_drawn (last frame)
 *   out[12..14] = 640x480 avg cycles/frame (prep, hw wait, mmio) on completion;
 *                 out[12..15] = trap info if faulted (0x80000000|cause, pc, val, ra)
 */
#include <stdint.h>
#include "ae350_address_map.h"
#include "ae350_dma.h"
#include "ae350_video.h"
#include "ae350_gfx2d.h"
#include "ae350_gfx3d.h"
#include "GL/gl.h"
#include "zbuffer.h"
#include "zgl.h"

#define CPU_HZ 800000000ULL
#define BENCH_FRAMES 16
#define GRID_COLS 20
#define GRID_ROWS 25
/* 20 * 25 quads * 2 tris/quad = 1,000 triangles */

#define FB0_32_ADDR    0x09000000u
#define FB1_32_ADDR    0x09200000u
#define BUF_320_COLOR  ((uint32_t *)(uintptr_t)0x09400000u)
#define BUF_ZBUFFER    ((uint16_t *)(uintptr_t)0x09500000u)

#define UART_DATA 0x20u
#define UART_LSR  0x34u
#define UART_REG(offset) (*(volatile uint32_t *)(AE350_UART_BASE + (offset)))

static volatile uint32_t *out = (volatile uint32_t *)(uintptr_t)AE350_RESULTS_BASE;
static int g_gfx2d_ok = 0;
static uint32_t g_tex_cfg_cached = 0;
static uint32_t g_last_z_cfg = 0xFFFFFFFFu;
static uint32_t g_last_tex_cfg = 0xFFFFFFFFu;
static uint64_t g_cyc_prep = 0;
static uint64_t g_cyc_wait = 0;
static uint64_t g_cyc_mmio = 0;

static void putc_uart(char c) {
    while (!(UART_REG(UART_LSR) & 0x20u)) {}
    UART_REG(UART_DATA) = (uint8_t)c;
}
static void puts_uart(const char *s) { while (*s) putc_uart(*s++); }
static void put_u32_dec(uint32_t v) {
    char buf[12];
    int n = 0;
    if (v == 0) { putc_uart('0'); return; }
    while (v > 0) { buf[n++] = (char)('0' + (v % 10u)); v /= 10u; }
    while (n > 0) putc_uart(buf[--n]);
}

__attribute__((noreturn)) void trap_report(unsigned cause, unsigned pc,
                                         unsigned value, unsigned ra, unsigned sp) {
    (void)sp;
    out[12] = 0x80000000u | cause;
    out[13] = pc;
    out[14] = value;
    out[15] = ra;
    for (;;) __asm__ volatile("wfi");
}

typedef struct {
    float x, y, z;
    float nx, ny, nz;
    float u, v;
} MeshVert;

static MeshVert g_mesh[(GRID_ROWS + 1) * (GRID_COLS + 1)];
static uint16_t g_tex[64 * 64];

static void gfx2d_clear_hook(void *user, uint16_t color, int w, int h,
                             uint16_t *pbuf, uint16_t *zbuf) {
    (void)user;
    (void)color;
    ae350_gfx3d_wait(0x100000u);
    if (pbuf) {
        /* 0xFF141E33 matches glClearColor(0.08, 0.12, 0.20, 1.0) in XRGB8888 */
        ae350_gfx2d_fill_rect_fmt((uint32_t)(uintptr_t)pbuf, (uint32_t)(w * 4),
                                  (uint32_t)w, (uint32_t)h, 0xFF141E33u,
                                  AE350_GFX2D_FMT_XRGB8888);
        ae350_gfx2d_wait(0xffffu);
    }
    if (zbuf) {
        ae350_gfx2d_fill_rect_fmt((uint32_t)(uintptr_t)zbuf, (uint32_t)(w * 2),
                                  (uint32_t)w, (uint32_t)h, 0xFFFFu,
                                  AE350_GFX2D_FMT_RGB565);
        ae350_gfx2d_wait(0xffffu);
    }
}

static void gfx3d_hw_set_tex(ZBuffer *zb, const uint16_t *tex, int w, int h) {
    (void)zb;
    ae350_gfx3d_upload_tex_rgb565(tex, w, h, 1,
                                  AE350_G3D_WRAP_REPEAT, AE350_G3D_WRAP_REPEAT);
    g_tex_cfg_cached = AE350_G3D_TEX_CFG;
    g_last_tex_cfg = g_tex_cfg_cached;
}

static inline int16_t to_q12_4(float v) {
    return (int16_t)(v * 16.0f + (v >= 0.0f ? 0.5f : -0.5f));
}

static inline uint32_t to_uq16_16(float z) {
    if (z <= 0.0f) return 0u;
    if (z >= 65535.0f) return 0xFFFF0000u;
    return (uint32_t)(z * 65536.0f);
}

static inline uint32_t pack_rgba(const ZBufferPoint *p) {
    int r = p->r >> 8;
    int g = p->g >> 8;
    int b = p->b >> 8;
    if (r < 0) r = 0; else if (r > 255) r = 255;
    if (g < 0) g = 0; else if (g > 255) g = 255;
    if (b < 0) b = 0; else if (b > 255) b = 255;
    return 0xFF000000u | ((uint32_t)r << 16) | ((uint32_t)g << 8) | (uint32_t)b;
}

static inline uint32_t clamp_q_uq2_30(float q) {
    if (q <= (1.0f / 256.0f)) return 0x00400000u;
    if (q >= 1.0f) return 0x40000000u;
    uint32_t v = (uint32_t)(q * 1073741824.0f + 0.5f);
    if (v < 0x00400000u) v = 0x00400000u;
    if (v > 0x40000000u) v = 0x40000000u;
    return v;
}

static void gfx3d_hw_draw_tri(ZBuffer *zb, const ZBufferPoint *p0,
                              const ZBufferPoint *p1, const ZBufferPoint *p2,
                              int xmin, int ymin, int xmax, int ymax,
                              int use_tex, int smooth) {
    uint64_t cy0 = tgl_rdcycle();
    float w0 = p0->w, w1 = p1->w, w2 = p2->w;
    float w_min = w0;
    if (w1 < w_min) w_min = w1;
    if (w2 < w_min) w_min = w2;
    if (w_min <= 0.0f) return;

    float q0 = w_min / w0;
    float q1 = w_min / w1;
    float q2 = w_min / w2;
    if (q0 < (1.0f / 256.0f)) q0 = 1.0f / 256.0f; else if (q0 > 1.0f) q0 = 1.0f;
    if (q1 < (1.0f / 256.0f)) q1 = 1.0f / 256.0f; else if (q1 > 1.0f) q1 = 1.0f;
    if (q2 < (1.0f / 256.0f)) q2 = 1.0f / 256.0f; else if (q2 > 1.0f) q2 = 1.0f;

    float tw = (float)zb->tex_w * 1048576.0f;
    float th = (float)zb->tex_h * 1048576.0f;
    int32_t s0 = use_tex ? (int32_t)(p0->u * tw * q0) : 0;
    int32_t t0 = use_tex ? (int32_t)(p0->v * th * q0) : 0;
    int32_t s1 = use_tex ? (int32_t)(p1->u * tw * q1) : 0;
    int32_t t1 = use_tex ? (int32_t)(p1->v * th * q1) : 0;
    int32_t s2 = use_tex ? (int32_t)(p2->u * tw * q2) : 0;
    int32_t t2 = use_tex ? (int32_t)(p2->v * th * q2) : 0;

    uint32_t q0_u = clamp_q_uq2_30(q0);
    uint32_t q1_u = clamp_q_uq2_30(q1);
    uint32_t q2_u = clamp_q_uq2_30(q2);

    uint32_t c0 = (use_tex && zb->tex_env_replace) ? 0xFFFFFFFFu : pack_rgba(p0);
    uint32_t c1 = (use_tex && zb->tex_env_replace) ? 0xFFFFFFFFu : (smooth ? pack_rgba(p1) : c0);
    uint32_t c2 = (use_tex && zb->tex_env_replace) ? 0xFFFFFFFFu : (smooth ? pack_rgba(p2) : c0);

    uint32_t xy0 = ((uint32_t)(uint16_t)to_q12_4(p0->sy) << 16) | (uint16_t)to_q12_4(p0->sx);
    uint32_t xy1 = ((uint32_t)(uint16_t)to_q12_4(p1->sy) << 16) | (uint16_t)to_q12_4(p1->sx);
    uint32_t xy2 = ((uint32_t)(uint16_t)to_q12_4(p2->sy) << 16) | (uint16_t)to_q12_4(p2->sx);

    uint32_t z0 = to_uq16_16(p0->fz);
    uint32_t z1 = to_uq16_16(p1->fz);
    uint32_t z2 = to_uq16_16(p2->fz);

    uint32_t flags = AE350_G3D_FLAG_PERSP | (smooth ? AE350_G3D_FLAG_GOURAUD : 0u);
    uint32_t z_cfg = (zb->depth_test ? (AE350_G3D_Z_TEST_EN | AE350_G3D_Z_FUNC_LESS) : AE350_G3D_Z_FUNC_ALWAYS) |
                     (zb->depth_write ? AE350_G3D_Z_WRITE_EN : 0u) |
                     AE350_G3D_COLOR_WRITE_EN;
    uint32_t tex_cfg = use_tex ? g_tex_cfg_cached : (g_tex_cfg_cached & ~AE350_G3D_TEX_EN);

    uint64_t cy1 = tgl_rdcycle();
    g_cyc_prep += cy1 - cy0;
    ae350_gfx3d_wait(0x100000u);
    uint64_t cy2 = tgl_rdcycle();
    g_cyc_wait += cy2 - cy1;

    if (z_cfg != g_last_z_cfg) {
        AE350_G3D_Z_CFG = z_cfg;
        g_last_z_cfg = z_cfg;
    }
    if (tex_cfg != g_last_tex_cfg) {
        AE350_G3D_TEX_CFG = tex_cfg;
        g_last_tex_cfg = tex_cfg;
    }

    AE350_G3D_TRI(0)  = flags;
    AE350_G3D_TRI(1)  = 0u;
    AE350_G3D_TRI(2)  = 0u;
    AE350_G3D_TRI(3)  = xy0;
    AE350_G3D_TRI(4)  = z0;
    AE350_G3D_TRI(5)  = (uint32_t)s0;
    AE350_G3D_TRI(6)  = (uint32_t)t0;
    AE350_G3D_TRI(7)  = q0_u;
    AE350_G3D_TRI(8)  = c0;
    AE350_G3D_TRI(9)  = xy1;
    AE350_G3D_TRI(10) = z1;
    AE350_G3D_TRI(11) = (uint32_t)s1;
    AE350_G3D_TRI(12) = (uint32_t)t1;
    AE350_G3D_TRI(13) = q1_u;
    AE350_G3D_TRI(14) = c1;
    AE350_G3D_TRI(15) = xy2;
    AE350_G3D_TRI(16) = z2;
    AE350_G3D_TRI(17) = (uint32_t)s2;
    AE350_G3D_TRI(18) = (uint32_t)t2;
    AE350_G3D_TRI(19) = q2_u;
    AE350_G3D_TRI(20) = c2;
    AE350_G3D_TRI(21) = ((uint32_t)(uint16_t)ymin << 16) | (uint16_t)xmin;
    AE350_G3D_TRI(22) = ((uint32_t)(uint16_t)ymax << 16) | (uint16_t)xmax;
    AE350_G3D_TRI(23) = 0u;
    AE350_G3D_CTRL    = 1u;
    g_cyc_mmio += tgl_rdcycle() - cy2;
}

static void build_texture(void) {
    for (int y = 0; y < 64; y++) {
        for (int x = 0; x < 64; x++) {
            int chk = ((x >> 3) ^ (y >> 3)) & 1;
            int r = chk ? (24 + (x >> 3)) : (8 + (y >> 3));
            int g = chk ? (48 + (y >> 2)) : (20 + (x >> 2));
            int b = chk ? 31 : (12 + ((x + y) >> 3));
            if (r > 31) r = 31;
            if (g > 63) g = 63;
            if (b > 31) b = 31;
            g_tex[y * 64 + x] = (uint16_t)((r << 11) | (g << 5) | b);
        }
    }
}

static void build_mesh(void) {
    for (int j = 0; j <= GRID_ROWS; j++) {
        float v = (float)j / (float)GRID_ROWS;
        float py = (v - 0.5f) * 2.2f;
        for (int i = 0; i <= GRID_COLS; i++) {
            float u = (float)i / (float)GRID_COLS;
            float px = (u - 0.5f) * 2.6f;
            float pz = 0.25f * tgl_sinf(px * 3.0f) * tgl_cosf(py * 3.0f);
            float dx = 0.75f * tgl_cosf(px * 3.0f) * tgl_cosf(py * 3.0f);
            float dy = -0.75f * tgl_sinf(px * 3.0f) * tgl_sinf(py * 3.0f);
            float nx = -dx, ny = -dy, nz = 1.0f;
            float inv = 1.0f / tgl_sqrtf(nx * nx + ny * ny + nz * nz);
            MeshVert *mv = &g_mesh[j * (GRID_COLS + 1) + i];
            mv->x = px; mv->y = py; mv->z = pz;
            mv->nx = nx * inv; mv->ny = ny * inv; mv->nz = nz * inv;
            mv->u = u * 4.0f; mv->v = v * 4.0f;
        }
    }
}

static void draw_mesh(float angle_deg) {
    glMatrixMode(GL_MODELVIEW);
    glLoadIdentity();
    glTranslatef(0.0f, 0.0f, -3.4f);
    glRotatef(25.0f, 1.0f, 0.0f, 0.0f);
    glRotatef(angle_deg, 0.0f, 1.0f, 0.0f);

    glBegin(GL_TRIANGLES);
    for (int j = 0; j < GRID_ROWS; j++) {
        const MeshVert *row0 = &g_mesh[j * (GRID_COLS + 1)];
        const MeshVert *row1 = &g_mesh[(j + 1) * (GRID_COLS + 1)];
        for (int i = 0; i < GRID_COLS; i++) {
            const MeshVert *v00 = &row0[i];
            const MeshVert *v10 = &row0[i + 1];
            const MeshVert *v01 = &row1[i];
            const MeshVert *v11 = &row1[i + 1];

            glNormal3f(v00->nx, v00->ny, v00->nz);
            glTexCoord2f(v00->u, v00->v);
            glVertex3f(v00->x, v00->y, v00->z);
            glNormal3f(v10->nx, v10->ny, v10->nz);
            glTexCoord2f(v10->u, v10->v);
            glVertex3f(v10->x, v10->y, v10->z);
            glNormal3f(v11->nx, v11->ny, v11->nz);
            glTexCoord2f(v11->u, v11->v);
            glVertex3f(v11->x, v11->y, v11->z);

            glNormal3f(v00->nx, v00->ny, v00->nz);
            glTexCoord2f(v00->u, v00->v);
            glVertex3f(v00->x, v00->y, v00->z);
            glNormal3f(v11->nx, v11->ny, v11->nz);
            glTexCoord2f(v11->u, v11->v);
            glVertex3f(v11->x, v11->y, v11->z);
            glNormal3f(v01->nx, v01->ny, v01->nz);
            glTexCoord2f(v01->u, v01->v);
            glVertex3f(v01->x, v01->y, v01->z);
        }
    }
    glEnd();
}

static void setup_gl(ZBuffer *zb, int w, int h) {
    ae350_gfx3d_init();
    g_last_z_cfg = 0xFFFFFFFFu;
    g_last_tex_cfg = 0xFFFFFFFFu;
    ZB_setHwBackend(zb, gfx3d_hw_draw_tri, gfx3d_hw_set_tex);

    glInit(zb);
    glSetClearHook(gfx2d_clear_hook, 0);
    glClearColor(0.08f, 0.12f, 0.20f, 1.0f);
    glViewport(0, 0, w, h);

    glMatrixMode(GL_PROJECTION);
    glLoadIdentity();
    gluPerspective(45.0, (double)w / (double)h, 0.5, 20.0);

    glMatrixMode(GL_MODELVIEW);
    glLoadIdentity();

    static const GLfloat light_pos[4] = { 1.0f, 2.0f, 2.0f, 0.0f };
    static const GLfloat light_diff[4] = { 1.0f, 1.0f, 1.0f, 1.0f };
    static const GLfloat light_amb[4] = { 0.25f, 0.25f, 0.25f, 1.0f };
    static const GLfloat mat_diff[4] = { 1.0f, 1.0f, 1.0f, 1.0f };
    glLightfv(GL_LIGHT0, GL_POSITION, light_pos);
    glLightfv(GL_LIGHT0, GL_DIFFUSE, light_diff);
    glLightfv(GL_LIGHT0, GL_AMBIENT, light_amb);
    glMaterialfv(GL_FRONT_AND_BACK, GL_AMBIENT_AND_DIFFUSE, mat_diff);

    glEnable(GL_DEPTH_TEST);
    glEnable(GL_LIGHTING);
    glEnable(GL_LIGHT0);
    glEnable(GL_TEXTURE_2D);
    glDisable(GL_CULL_FACE);
    glShadeModel(GL_SMOOTH);

    GLuint tex_id = 0;
    glGenTextures(1, &tex_id);
    glBindTexture(GL_TEXTURE_2D, tex_id);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB, 64, 64, 0, GL_RGB, GL_UNSIGNED_SHORT_5_6_5, g_tex);
}

static void run_bench_320x240(void) {
    ZBuffer *zb = ZB_open(320, 240, (uint16_t *)0, BUF_ZBUFFER);
    ZB_setTarget32(zb, BUF_320_COLOR);
    ae350_gfx3d_set_target((uint32_t)(uintptr_t)BUF_320_COLOR, 320u,
                           (uint32_t)(uintptr_t)BUF_ZBUFFER);
    setup_gl(zb, 320, 240);
    glResetStats();

    uint64_t t_start = tgl_rdcycle();
    for (int f = 0; f < BENCH_FRAMES; f++) {
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT);
        draw_mesh((float)(f * 6));
    }
    ae350_gfx3d_wait(0x100000u);
    uint64_t total_cycles = tgl_rdcycle() - t_start;

    if (g_gfx2d_ok) {
        uint32_t *hdmi_fb = (uint32_t *)ae350_video_frame_data(0);
        if (hdmi_fb) {
            ae350_gfx2d_scale((uint32_t)(uintptr_t)BUF_320_COLOR, 1280u, 320u, 240u,
                              AE350_GFX2D_FMT_XRGB8888,
                              (uint32_t)(uintptr_t)hdmi_fb, 2560u, 640u, 480u,
                              AE350_GFX2D_FMT_XRGB8888,
                              0u, 0u, 0x00008000u, 0x00008000u,
                              0xFFu, AE350_GFX2D_FLAG_BILINEAR);
            ae350_gfx2d_wait(0xffffu);
        }
        ae350_video_present(0);
    }
    TinyGLStats st;
    glGetStats(&st);

    uint64_t avg_tot = total_cycles / BENCH_FRAMES;
    uint32_t fps_x100 = (uint32_t)((CPU_HZ * 100ULL * BENCH_FRAMES) / (total_cycles ? total_cycles : 1ULL));
    out[2] = fps_x100;
    out[3] = (uint32_t)avg_tot;
    out[4] = (uint32_t)((st.cycles_vertex + st.cycles_clip) / BENCH_FRAMES);
    out[5] = (uint32_t)(st.cycles_raster / BENCH_FRAMES);
    out[10] = st.tris_submitted / BENCH_FRAMES;
    out[11] = st.tris_drawn / BENCH_FRAMES;

    puts_uart("320x240 HW FPS="); put_u32_dec(fps_x100 / 100u); putc_uart('.');
    put_u32_dec(fps_x100 % 100u);
    puts_uart(" cyc="); put_u32_dec((uint32_t)avg_tot);
    puts_uart(" tris="); put_u32_dec(out[11]); puts_uart("\r\n");
}

static void run_bench_640x480(void) {
    uint32_t *fb = (uint32_t *)ae350_video_frame_data(1);
    ZBuffer *zb = ZB_open(640, 480, (uint16_t *)0, BUF_ZBUFFER);
    ZB_setTarget32(zb, fb);
    ae350_gfx3d_set_target((uint32_t)(uintptr_t)fb, 640u,
                           (uint32_t)(uintptr_t)BUF_ZBUFFER);
    setup_gl(zb, 640, 480);
    glResetStats();
    g_cyc_prep = 0; g_cyc_wait = 0; g_cyc_mmio = 0;

    uint64_t t_start = tgl_rdcycle();
    for (int f = 0; f < BENCH_FRAMES; f++) {
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT);
        draw_mesh((float)(f * 6));
    }
    ae350_gfx3d_wait(0x100000u);
    uint64_t total_cycles = tgl_rdcycle() - t_start;
    ae350_video_present(1);

    TinyGLStats st;
    glGetStats(&st);

    uint64_t avg_tot = total_cycles / BENCH_FRAMES;
    uint32_t fps_x100 = (uint32_t)((CPU_HZ * 100ULL * BENCH_FRAMES) / (total_cycles ? total_cycles : 1ULL));
    out[6] = fps_x100;
    out[7] = (uint32_t)avg_tot;
    out[8] = (uint32_t)((st.cycles_vertex + st.cycles_clip) / BENCH_FRAMES);
    out[9] = (uint32_t)(st.cycles_raster / BENCH_FRAMES);
    out[12] = (uint32_t)(g_cyc_prep / BENCH_FRAMES);
    out[13] = (uint32_t)(g_cyc_wait / BENCH_FRAMES);
    out[14] = (uint32_t)(g_cyc_mmio / BENCH_FRAMES);

    puts_uart("640x480 HW FPS="); put_u32_dec(fps_x100 / 100u); putc_uart('.');
    put_u32_dec(fps_x100 % 100u);
    puts_uart(" cyc="); put_u32_dec((uint32_t)avg_tot);
    puts_uart(" vtx="); put_u32_dec(out[8]);
    puts_uart(" rst="); put_u32_dec(out[9]);
    puts_uart("\r\n");
}

int main(void) {
    __asm__ volatile("csrw 0x7cc, %0\ncsrs 0x7ca, %1" :: "r"(23), "r"(3) : "memory");
    for (unsigned i = 0; i < 16; i++) out[i] = 0;
    out[0] = 0xC3900000u;

    puts_uart("\r\n=== AE350 TinyGL GFX3D HW Bench ===\r\n");

    if (ae350_video_set_mode(0, AE350_VIDEO_XRGB8888) ||
        ae350_video_init_buffers((void *)(uintptr_t)FB0_32_ADDR,
                                 (void *)(uintptr_t)FB1_32_ADDR, 2560)) {
        puts_uart("FAIL: video init\r\n");
        out[0] = 0xC39000FFu;
        return 0;
    }
    g_gfx2d_ok = (ae350_gfx2d_probe() == AE350_GFX2D_OK) ? 1 : 0;
    out[1] = (uint32_t)g_gfx2d_ok;

    build_texture();
    build_mesh();

    run_bench_320x240();
    run_bench_640x480();

    out[0] = 0xC3900001u;
    puts_uart("BENCH DONE\r\n");

    /* Continuous interactive display loop at 640x480 XRGB8888 double-buffered */
    ZBuffer *zb = ZB_open(640, 480, (uint16_t *)0, BUF_ZBUFFER);
    setup_gl(zb, 640, 480);
    unsigned frame = 0;
    for (;;) {
        unsigned slot = frame & 1u;
        uint32_t *hdmi_fb = (uint32_t *)ae350_video_frame_data(slot);
        ZB_setTarget32(zb, hdmi_fb);
        ae350_gfx3d_set_target((uint32_t)(uintptr_t)hdmi_fb, 640u,
                               (uint32_t)(uintptr_t)BUF_ZBUFFER);
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT);
        draw_mesh((float)((frame * 3) % 360));
        ae350_gfx3d_wait(0x100000u);
        ae350_video_present(slot);
        frame++;
    }
}
