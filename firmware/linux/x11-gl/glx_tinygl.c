/*
 * Minimal GLX 1.4 implementation binding TinyGL to X11 (Xfbdev) with two
 * selectable rasterization backends:
 *   AE350_GFX3D=0 -> SW-TinyGL (CPU RGB565 ZBuffer -> XShm/XPutImage)
 *   AE350_GFX3D=1 -> HW-AE350-GFX3D (/dev/ae350-gfx3d batched ioctl + readback)
 */
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/ipc.h>
#include <sys/shm.h>
#include <unistd.h>

#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <X11/extensions/XShm.h>

#include "GL/gl.h"
#include "GL/glx.h"
#include "zbuffer.h"
#include "zgl.h"
#include "ae350_gfx3d_uapi.h"

#define HW_BATCH_CAP 512u

#define G3D_FLAG_PERSP    (1u << 10)
#define G3D_FLAG_GOURAUD  (1u << 11)
#define G3D_TEX_EN        (1u << 0)
#define G3D_BILINEAR_EN   (1u << 1)
#define G3D_Z_TEST_EN     (1u << 0)
#define G3D_Z_WRITE_EN    (1u << 1)
#define G3D_Z_FUNC_LESS   (1u << 2)
#define G3D_Z_FUNC_ALWAYS (7u << 2)
#define G3D_COLOR_WR_EN   (1u << 5)

struct __GLXcontextRec {
	Display *dpy;
	int screen;
	Visual *visual;
	int depth;
	int width;
	int height;
	GLXDrawable drawable;
	GC gc;
	XImage *ximg;
	XShmSegmentInfo shminfo;
	Bool use_shm;
	uint16_t *sw_pbuf;
	uint16_t *sw_zbuf;
	ZBuffer *zb;
	int hw_mode;
	int gfx_fd;
	uint32_t tex_cfg_cached;
	struct ae350_gfx3d_tri batch[HW_BATCH_CAP];
	uint32_t batch_cnt;
	unsigned long last_pixels_out;
};

static GLXContext g_cur_ctx = NULL;
static volatile int g_xerr_seen = 0;

static int shm_err_handler(Display *dpy, XErrorEvent *ev)
{
	(void)dpy;
	(void)ev;
	g_xerr_seen = 1;
	return 0;
}

static uint32_t ilog2_u32(int v)
{
	uint32_t r = 0;

	while (v > 1) {
		v >>= 1;
		r++;
	}
	return r;
}

static inline int16_t to_q12_4(float v)
{
	return (int16_t)(v * 16.0f + (v >= 0.0f ? 0.5f : -0.5f));
}

static inline uint32_t to_uq16_16(float z)
{
	if (z <= 0.0f)
		return 0u;
	if (z >= 65535.0f)
		return 0xFFFF0000u;
	return (uint32_t)(z * 65536.0f);
}

static inline uint32_t clamp_q_uq2_30(float q)
{
	if (q <= (1.0f / 256.0f))
		return 0x00400000u;
	if (q >= 1.0f)
		return 0x40000000u;
	uint32_t v = (uint32_t)(q * 1073741824.0f + 0.5f);
	if (v < 0x00400000u)
		v = 0x00400000u;
	if (v > 0x40000000u)
		v = 0x40000000u;
	return v;
}

static inline uint32_t pack_rgba_point(const ZBufferPoint *p)
{
	int r = p->r >> 8;
	int g = p->g >> 8;
	int b = p->b >> 8;

	if (r < 0) r = 0; else if (r > 255) r = 255;
	if (g < 0) g = 0; else if (g > 255) g = 255;
	if (b < 0) b = 0; else if (b > 255) b = 255;
	return 0xFF000000u | ((uint32_t)r << 16) | ((uint32_t)g << 8) | (uint32_t)b;
}

static uint32_t rgb565_to_xrgb8888(uint16_t p)
{
	uint32_t r5 = (p >> 11) & 0x1Fu;
	uint32_t g6 = (p >> 5) & 0x3Fu;
	uint32_t b5 = p & 0x1Fu;
	uint32_t r8 = (r5 << 3) | (r5 >> 2);
	uint32_t g8 = (g6 << 2) | (g6 >> 4);
	uint32_t b8 = (b5 << 3) | (b5 >> 2);

	return 0xFF000000u | (r8 << 16) | (g8 << 8) | b8;
}

static void hw_flush_batch(GLXContext ctx)
{
	struct ae350_gfx3d_submit sub;

	if (!ctx || !ctx->hw_mode || ctx->gfx_fd < 0 || ctx->batch_cnt == 0u)
		return;
	memset(&sub, 0, sizeof(sub));
	sub.width = (uint32_t)ctx->width;
	sub.height = (uint32_t)ctx->height;
	sub.count = ctx->batch_cnt;
	sub.tris_ptr = (uint64_t)(uintptr_t)ctx->batch;
	if (ioctl(ctx->gfx_fd, AE350_GFX3D_IOC_SUBMIT, &sub) == 0)
		ctx->last_pixels_out += sub.pixels_out;
	ctx->batch_cnt = 0u;
}

static void hw_clear_hook(void *user, uint16_t color, int w, int h,
			  uint16_t *pbuf, uint16_t *zbuf)
{
	GLXContext ctx = (GLXContext)user;
	struct ae350_gfx3d_clear clr;

	if (!ctx || !ctx->hw_mode || ctx->gfx_fd < 0)
		return;
	hw_flush_batch(ctx);
	memset(&clr, 0, sizeof(clr));
	clr.width = (uint32_t)w;
	clr.height = (uint32_t)h;
	clr.clear_color = pbuf ? 1u : 0u;
	clr.clear_z = zbuf ? 1u : 0u;
	clr.color_xrgb8888 = rgb565_to_xrgb8888(color);
	clr.z_val = 0xFFFFu;
	ioctl(ctx->gfx_fd, AE350_GFX3D_IOC_CLEAR, &clr);
}

static void hw_set_tex(ZBuffer *zb, const uint16_t *tex, int w, int h)
{
	GLXContext ctx = g_cur_ctx;
	struct ae350_gfx3d_tex req;

	(void)zb;
	if (!ctx || !ctx->hw_mode || ctx->gfx_fd < 0)
		return;
	hw_flush_batch(ctx);
	memset(&req, 0, sizeof(req));
	if (!tex || w <= 0 || h <= 0) {
		ctx->tex_cfg_cached = 0u;
		ioctl(ctx->gfx_fd, AE350_GFX3D_IOC_UPLOAD_TEX, &req);
		return;
	}
	req.width = (uint32_t)w;
	req.height = (uint32_t)h;
	req.bilinear = 1u;
	req.wrap_u = 0u;
	req.wrap_v = 0u;
	req.rgb565_ptr = (uint64_t)(uintptr_t)tex;
	if (ioctl(ctx->gfx_fd, AE350_GFX3D_IOC_UPLOAD_TEX, &req) == 0) {
		ctx->tex_cfg_cached = G3D_TEX_EN | G3D_BILINEAR_EN |
				      ((ilog2_u32(w) & 0xFu) << 7) |
				      ((ilog2_u32(h) & 0xFu) << 11);
	}
}

static void hw_draw_tri(ZBuffer *zb, const ZBufferPoint *p0,
			const ZBufferPoint *p1, const ZBufferPoint *p2,
			int xmin, int ymin, int xmax, int ymax,
			int use_tex, int smooth)
{
	GLXContext ctx = g_cur_ctx;
	float w0 = p0->w, w1 = p1->w, w2 = p2->w;
	float w_min = w0;
	struct ae350_gfx3d_tri *t;

	if (!ctx || !ctx->hw_mode || ctx->gfx_fd < 0)
		return;
	if (w1 < w_min) w_min = w1;
	if (w2 < w_min) w_min = w2;
	if (w_min <= 0.0f)
		return;

	float q0 = w_min / w0, q1 = w_min / w1, q2 = w_min / w2;
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

	uint32_t c0, c1, c2;
	if (use_tex && zb->tex_env_replace) {
		c0 = c1 = c2 = 0xFFFFFFFFu;
	} else if (smooth) {
		c0 = pack_rgba_point(p0);
		c1 = pack_rgba_point(p1);
		c2 = pack_rgba_point(p2);
	} else {
		c0 = c1 = c2 = pack_rgba_point(p2);
	}

	if (ctx->batch_cnt >= HW_BATCH_CAP)
		hw_flush_batch(ctx);

	t = &ctx->batch[ctx->batch_cnt++];
	t->z_cfg = (zb->depth_test ? (G3D_Z_TEST_EN | G3D_Z_FUNC_LESS) : G3D_Z_FUNC_ALWAYS) |
		   (zb->depth_write ? G3D_Z_WRITE_EN : 0u) |
		   G3D_COLOR_WR_EN;
	t->tex_cfg = use_tex ? ctx->tex_cfg_cached : (ctx->tex_cfg_cached & ~G3D_TEX_EN);
	t->words[0]  = G3D_FLAG_PERSP | (smooth ? G3D_FLAG_GOURAUD : 0u);
	t->words[1]  = 0u;
	t->words[2]  = 0u;
	t->words[3]  = ((uint32_t)(uint16_t)to_q12_4(p0->sy) << 16) | (uint16_t)to_q12_4(p0->sx);
	t->words[4]  = to_uq16_16(p0->fz);
	t->words[5]  = (uint32_t)s0;
	t->words[6]  = (uint32_t)t0;
	t->words[7]  = clamp_q_uq2_30(q0);
	t->words[8]  = c0;
	t->words[9]  = ((uint32_t)(uint16_t)to_q12_4(p1->sy) << 16) | (uint16_t)to_q12_4(p1->sx);
	t->words[10] = to_uq16_16(p1->fz);
	t->words[11] = (uint32_t)s1;
	t->words[12] = (uint32_t)t1;
	t->words[13] = clamp_q_uq2_30(q1);
	t->words[14] = c1;
	t->words[15] = ((uint32_t)(uint16_t)to_q12_4(p2->sy) << 16) | (uint16_t)to_q12_4(p2->sx);
	t->words[16] = to_uq16_16(p2->fz);
	t->words[17] = (uint32_t)s2;
	t->words[18] = (uint32_t)t2;
	t->words[19] = clamp_q_uq2_30(q2);
	t->words[20] = c2;
	t->words[21] = ((uint32_t)(uint16_t)ymin << 16) | (uint16_t)xmin;
	t->words[22] = ((uint32_t)(uint16_t)ymax << 16) | (uint16_t)xmax;
	t->words[23] = 0u;
}

static void release_surface(GLXContext ctx)
{
	if (!ctx)
		return;
	hw_flush_batch(ctx);
	if (ctx->zb) {
		ZB_close(ctx->zb);
		ctx->zb = NULL;
	}
	if (ctx->use_shm && ctx->dpy && ctx->ximg) {
		XShmDetach(ctx->dpy, &ctx->shminfo);
		XSync(ctx->dpy, False);
		shmdt(ctx->shminfo.shmaddr);
		shmctl(ctx->shminfo.shmid, IPC_RMID, NULL);
		ctx->ximg->data = NULL;
		XDestroyImage(ctx->ximg);
		ctx->use_shm = False;
		ctx->ximg = NULL;
	} else if (ctx->ximg) {
		XDestroyImage(ctx->ximg); /* frees ximg->data */
		ctx->ximg = NULL;
	}
	free(ctx->sw_pbuf);
	ctx->sw_pbuf = NULL;
	free(ctx->sw_zbuf);
	ctx->sw_zbuf = NULL;
	if (ctx->gc && ctx->dpy) {
		XFreeGC(ctx->dpy, ctx->gc);
		ctx->gc = NULL;
	}
	ctx->width = 0;
	ctx->height = 0;
}

static Bool ensure_surface(GLXContext ctx, Display *dpy, GLXDrawable drawable,
			   int w, int h)
{
	if (ctx->ximg && ctx->width == w && ctx->height == h && ctx->drawable == drawable)
		return True;
	release_surface(ctx);
	ctx->dpy = dpy;
	ctx->drawable = drawable;
	ctx->width = w;
	ctx->height = h;
	ctx->gc = XCreateGC(dpy, drawable, 0, NULL);

	if (XShmQueryExtension(dpy)) {
		XImage *img = XShmCreateImage(dpy, ctx->visual, (unsigned)ctx->depth,
					      ZPixmap, NULL, &ctx->shminfo,
					      (unsigned)w, (unsigned)h);
		if (img) {
			size_t sz = (size_t)img->bytes_per_line * (size_t)img->height;
			int shmid = shmget(IPC_PRIVATE, sz, IPC_CREAT | 0666);
			if (shmid >= 0) {
				void *addr = shmat(shmid, NULL, 0);
				if (addr != (void *)-1) {
					XErrorHandler old_err;
					ctx->shminfo.shmid = shmid;
					ctx->shminfo.shmaddr = (char *)addr;
					ctx->shminfo.readOnly = False;
					img->data = (char *)addr;
					XSync(dpy, False);
					g_xerr_seen = 0;
					old_err = XSetErrorHandler(shm_err_handler);
					XShmAttach(dpy, &ctx->shminfo);
					XSync(dpy, False);
					XSetErrorHandler(old_err);
					if (!g_xerr_seen) {
						ctx->ximg = img;
						ctx->use_shm = True;
					} else {
						shmdt(addr);
						shmctl(shmid, IPC_RMID, NULL);
						img->data = NULL;
						XDestroyImage(img);
					}
				} else {
					shmctl(shmid, IPC_RMID, NULL);
					XDestroyImage(img);
				}
			} else {
				XDestroyImage(img);
			}
		}
	}

	if (!ctx->ximg) {
		size_t stride = ((size_t)w * 2u + 3u) & ~(size_t)3u;
		char *buf = (char *)calloc(stride, (size_t)h);
		if (!buf)
			return False;
		ctx->ximg = XCreateImage(dpy, ctx->visual, (unsigned)ctx->depth,
					 ZPixmap, 0, buf, (unsigned)w,
					 (unsigned)h, 16, (int)stride);
		if (!ctx->ximg) {
			free(buf);
			return False;
		}
		ctx->use_shm = False;
	}

	if (ctx->ximg->bits_per_pixel != 16 ||
	    ctx->ximg->bytes_per_line != w * 2) {
		ctx->sw_pbuf = (uint16_t *)calloc((size_t)w * (size_t)h, sizeof(uint16_t));
		if (!ctx->sw_pbuf)
			return False;
	}
	ctx->sw_zbuf = (uint16_t *)malloc((size_t)w * (size_t)h * sizeof(uint16_t));
	if (!ctx->sw_zbuf)
		return False;

	uint16_t *render_pbuf = ctx->sw_pbuf ? ctx->sw_pbuf : (uint16_t *)ctx->ximg->data;
	ctx->zb = ZB_open(w, h, render_pbuf, ctx->sw_zbuf);
	glInit(ctx->zb);
	if (ctx->hw_mode) {
		glSetClearHook(hw_clear_hook, ctx);
		ZB_setHwBackend(ctx->zb, hw_draw_tri, hw_set_tex);
	}
	return True;
}

Bool glXQueryVersion(Display *dpy, int *major, int *minor)
{
	(void)dpy;
	if (major) *major = 1;
	if (minor) *minor = 4;
	return True;
}

const char *glXQueryExtensionsString(Display *dpy, int screen)
{
	(void)dpy;
	(void)screen;
	return "";
}

XVisualInfo *glXChooseVisual(Display *dpy, int screen, int *attribList)
{
	XVisualInfo tmpl;
	int n = 0;

	(void)attribList;
	memset(&tmpl, 0, sizeof(tmpl));
	tmpl.screen = screen;
	tmpl.depth = DefaultDepth(dpy, screen);
	tmpl.class = TrueColor;
	return XGetVisualInfo(dpy, VisualScreenMask | VisualDepthMask | VisualClassMask,
			      &tmpl, &n);
}

GLXContext glXCreateContext(Display *dpy, XVisualInfo *vis,
			    GLXContext shareList, Bool direct)
{
	struct __GLXcontextRec *ctx;
	const char *env = getenv("AE350_GFX3D");
	int want_hw = 1;

	(void)shareList;
	(void)direct;
	if (!dpy || !vis)
		return NULL;
	ctx = (struct __GLXcontextRec *)calloc(1, sizeof(*ctx));
	if (!ctx)
		return NULL;
	ctx->dpy = dpy;
	ctx->screen = vis->screen;
	ctx->visual = vis->visual;
	ctx->depth = vis->depth;
	ctx->gfx_fd = -1;

	if (env && strcmp(env, "0") == 0)
		want_hw = 0;
	if (want_hw) {
		int fd = open("/dev/ae350-gfx3d", O_RDWR);
		if (fd >= 0) {
			struct ae350_gfx3d_info info;
			if (ioctl(fd, AE350_GFX3D_IOC_INFO, &info) == 0) {
				ctx->gfx_fd = fd;
				ctx->hw_mode = 1;
			} else {
				close(fd);
			}
		}
	}
	return ctx;
}

void glXDestroyContext(Display *dpy, GLXContext ctx)
{
	(void)dpy;
	if (!ctx)
		return;
	if (g_cur_ctx == ctx)
		g_cur_ctx = NULL;
	release_surface(ctx);
	if (ctx->gfx_fd >= 0)
		close(ctx->gfx_fd);
	free(ctx);
}

Bool glXMakeCurrent(Display *dpy, GLXDrawable drawable, GLXContext ctx)
{
	Window root;
	int x, y;
	unsigned int w = 0, h = 0, border = 0, depth = 0;

	if (!ctx || !dpy || !drawable) {
		g_cur_ctx = NULL;
		return True;
	}
	if (!XGetGeometry(dpy, drawable, &root, &x, &y, &w, &h, &border, &depth))
		return False;
	if (ctx->hw_mode && (w > AE350_GFX3D_MAX_W || h > AE350_GFX3D_MAX_H))
		ctx->hw_mode = 0;
	g_cur_ctx = ctx;
	return ensure_surface(ctx, dpy, drawable, (int)w, (int)h);
}

static void convert_pbuf_to_ximg(XImage *img, const uint16_t *pbuf, int w, int h)
{
	int x, y;

	for (y = 0; y < h; y++) {
		const uint16_t *src = pbuf + y * w;
		for (x = 0; x < w; x++) {
			uint16_t c = src[x];
			unsigned long px = c;
			if (img->bits_per_pixel >= 24)
				px = rgb565_to_xrgb8888(c);
			XPutPixel(img, x, y, px);
		}
	}
}

void glXSwapBuffers(Display *dpy, GLXDrawable drawable)
{
	GLXContext ctx = g_cur_ctx;

	if (!ctx || !ctx->ximg)
		return;
	if (ctx->hw_mode && ctx->gfx_fd >= 0) {
		struct ae350_gfx3d_readback rb;
		hw_flush_batch(ctx);
		memset(&rb, 0, sizeof(rb));
		rb.width = (uint32_t)ctx->width;
		rb.height = (uint32_t)ctx->height;
		if (!ctx->sw_pbuf) {
			rb.dst_stride = (uint32_t)ctx->ximg->bytes_per_line;
			rb.fmt = AE350_GFX3D_READBACK_RGB565;
			rb.dst_ptr = (uint64_t)(uintptr_t)ctx->ximg->data;
		} else {
			rb.dst_stride = (uint32_t)ctx->width * 2u;
			rb.fmt = AE350_GFX3D_READBACK_RGB565;
			rb.dst_ptr = (uint64_t)(uintptr_t)ctx->sw_pbuf;
		}
		ioctl(ctx->gfx_fd, AE350_GFX3D_IOC_READBACK, &rb);
		if (ctx->sw_pbuf)
			convert_pbuf_to_ximg(ctx->ximg, ctx->sw_pbuf, ctx->width, ctx->height);
	} else if (ctx->sw_pbuf) {
		convert_pbuf_to_ximg(ctx->ximg, ctx->sw_pbuf, ctx->width, ctx->height);
	}

	if (ctx->use_shm) {
		XShmPutImage(dpy, drawable, ctx->gc, ctx->ximg, 0, 0, 0, 0,
			     (unsigned)ctx->width, (unsigned)ctx->height, False);
	} else {
		XPutImage(dpy, drawable, ctx->gc, ctx->ximg, 0, 0, 0, 0,
			  (unsigned)ctx->width, (unsigned)ctx->height);
	}
	XSync(dpy, False);
}

void glXWaitGL(void) { hw_flush_batch(g_cur_ctx); }
void glXWaitX(void) {}
GLXContext glXGetCurrentContext(void) { return g_cur_ctx; }
GLXDrawable glXGetCurrentDrawable(void) { return g_cur_ctx ? g_cur_ctx->drawable : 0; }

const char *ae350_glx_backend_name(void)
{
	if (!g_cur_ctx)
		return "none";
	return g_cur_ctx->hw_mode ? "HW-AE350-GFX3D" : "SW-TinyGL";
}

unsigned long ae350_glx_last_pixels_out(void)
{
	return g_cur_ctx ? g_cur_ctx->last_pixels_out : 0ul;
}
