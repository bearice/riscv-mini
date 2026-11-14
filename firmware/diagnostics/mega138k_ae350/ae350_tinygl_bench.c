/* AE350 TinyGL Stage 1 Benchmark:
 *   Single-texture 1,000-triangle rotating 3D mesh, single-light Gouraud shading,
 *   16-bit software Z-buffer, GFX2D FILL_RECT clear & double-buffered HDMI swap.
 *
 * Writes results to AE350_RESULTS_BASE (800 MHz CPU clock):
 *   out[0] = status (0xC3900000 running, 0xC3900001 done)
 *   out[1] = gfx2d_ok (1 if GFX2D probe succeeded)
 *   out[2] = 320x240 FPS (x100, e.g. 4520 = 45.20 FPS)
 *   out[3] = 320x240 avg cycles/frame (total)
 *   out[4] = 320x240 avg cycles/frame (vertex+clip)
 *   out[5] = 320x240 avg cycles/frame (raster)
 *   out[6] = 640x480 FPS (x100)
 *   out[7] = 640x480 avg cycles/frame (total)
 *   out[8] = 640x480 avg cycles/frame (vertex+clip)
 *   out[9] = 640x480 avg cycles/frame (raster)
 *   out[10] = tris_submitted (last frame)
 *   out[11] = tris_drawn (last frame)
 *   out[12..15] = trap info if faulted (0x80000000|cause, pc, val, ra)
 */
#include <stdint.h>
#include "ae350_address_map.h"
#include "ae350_dma.h"
#include "ae350_video.h"
#include "ae350_gfx2d.h"
#include "GL/gl.h"
#include "zbuffer.h"
#include "zgl.h"

#define CPU_HZ 800000000ULL
#define BENCH_FRAMES 16
#define GRID_COLS 20
#define GRID_ROWS 25
/* 20 * 25 quads * 2 tris/quad = 1,000 triangles */

#define BUF_320_COLOR ((uint16_t *)(uintptr_t)(AE350_DIAG_VIDEO_FB1 + 0x00100000u))
#define BUF_ZBUFFER   ((uint16_t *)(uintptr_t)(AE350_DIAG_VIDEO_FB1 + 0x00140000u))

static volatile uint32_t *out = (volatile uint32_t *)(uintptr_t)AE350_RESULTS_BASE;
static int g_gfx2d_ok = 0;

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
    if (!g_gfx2d_ok) {
        if (pbuf) {
            uint32_t c32 = ((uint32_t)color << 16) | color;
            uint32_t *pp = (uint32_t *)pbuf;
            int n32 = (w * h) >> 1;
            for (int i = 0; i < n32; i++) pp[i] = c32;
        }
        if (zbuf) {
            uint32_t *zp = (uint32_t *)zbuf;
            int n32 = (w * h) >> 1;
            for (int i = 0; i < n32; i++) zp[i] = 0xFFFFFFFFu;
        }
        return;
    }
    /* Invalidate D-cache once before GFX2D FILL_RECT so dirty lines are
     * discarded without writeback and CPU reads fresh cleared DDR3. */
    if (pbuf) {
        ae350_dma_sync_for_cpu((uintptr_t)pbuf, (uint32_t)(w * h * 2));
        ae350_gfx2d_fill_rect((uint32_t)(uintptr_t)pbuf, (uint32_t)(w * 2),
                              (uint32_t)w, (uint32_t)h, color);
        ae350_gfx2d_wait(0xffffu);
    }
    if (zbuf) {
        ae350_dma_sync_for_cpu((uintptr_t)zbuf, (uint32_t)(w * h * 2));
        ae350_gfx2d_fill_rect((uint32_t)(uintptr_t)zbuf, (uint32_t)(w * 2),
                              (uint32_t)w, (uint32_t)h, 0xFFFFu);
        ae350_gfx2d_wait(0xffffu);
    }
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
    /* Deformed heightfield grid sized to ~30% screen coverage at z = -3.2 */
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
    ZBuffer *zb = ZB_open(320, 240, BUF_320_COLOR, BUF_ZBUFFER);
    setup_gl(zb, 320, 240);
    glResetStats();

    uint64_t t_start = tgl_rdcycle();
    for (int f = 0; f < BENCH_FRAMES; f++) {
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT);
        draw_mesh((float)(f * 6));
    }
    uint64_t total_cycles = tgl_rdcycle() - t_start;
    if (g_gfx2d_ok) {
        uint16_t *hdmi_fb = ae350_video_frame(0);
        if (hdmi_fb) {
            ae350_dma_sync_for_device((uintptr_t)BUF_320_COLOR, 320u * 240u * 2u);
            ae350_gfx2d_scale((uint32_t)(uintptr_t)BUF_320_COLOR, 640u, 320u, 240u,
                              AE350_GFX2D_FMT_RGB565,
                              (uint32_t)(uintptr_t)hdmi_fb, 1280u, 640u, 480u,
                              AE350_GFX2D_FMT_RGB565,
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
}

static void run_bench_640x480(void) {
    ZBuffer *zb = ZB_open(640, 480, ae350_video_frame(0), BUF_ZBUFFER);
    setup_gl(zb, 640, 480);
    glResetStats();

    uint64_t t_start = tgl_rdcycle();
    uint16_t *fb = ae350_video_frame(1);
    ZB_setTarget(zb, fb);
    for (int f = 0; f < BENCH_FRAMES; f++) {
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT);
        draw_mesh((float)(f * 6));
    }
    uint64_t total_cycles = tgl_rdcycle() - t_start;
    ae350_dma_sync_for_device((uintptr_t)fb, 640u * 480u * 2u);
    ae350_video_present(1);
    TinyGLStats st;
    glGetStats(&st);

    uint64_t avg_tot = total_cycles / BENCH_FRAMES;
    uint32_t fps_x100 = (uint32_t)((CPU_HZ * 100ULL * BENCH_FRAMES) / (total_cycles ? total_cycles : 1ULL));
    out[6] = fps_x100;
    out[7] = (uint32_t)avg_tot;
    out[8] = (uint32_t)((st.cycles_vertex + st.cycles_clip) / BENCH_FRAMES);
    out[9] = (uint32_t)(st.cycles_raster / BENCH_FRAMES);
}

int main(void) {
    __asm__ volatile("csrw 0x7cc, %0\ncsrs 0x7ca, %1" :: "r"(23), "r"(3) : "memory");
    for (unsigned i = 0; i < 16; i++) out[i] = 0;
    out[0] = 0xC3900000u;

    if (ae350_video_init((uint16_t *)(uintptr_t)AE350_DIAG_VIDEO_FB0,
                         (uint16_t *)(uintptr_t)AE350_DIAG_VIDEO_FB1, 1280)) {
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

    /* Continuous interactive display loop at 320x240 scaled 2x to 640x480 */
    ZBuffer *zb = ZB_open(320, 240, BUF_320_COLOR, BUF_ZBUFFER);
    setup_gl(zb, 320, 240);
    unsigned frame = 0;
    for (;;) {
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT);
        draw_mesh((float)((frame * 3) % 360));
        unsigned slot = frame & 1u;
        uint16_t *hdmi_fb = ae350_video_frame(slot);
        if (g_gfx2d_ok && hdmi_fb) {
            ae350_dma_sync_for_device((uintptr_t)BUF_320_COLOR, 320u * 240u * 2u);
            ae350_gfx2d_scale((uint32_t)(uintptr_t)BUF_320_COLOR, 640u, 320u, 240u,
                              AE350_GFX2D_FMT_RGB565,
                              (uint32_t)(uintptr_t)hdmi_fb, 1280u, 640u, 480u,
                              AE350_GFX2D_FMT_RGB565,
                              0u, 0u, 0x00008000u, 0x00008000u,
                              0xFFu, AE350_GFX2D_FLAG_BILINEAR);
            ae350_gfx2d_wait(0xffffu);
        }
        ae350_video_present(slot);
        frame++;
    }
}
