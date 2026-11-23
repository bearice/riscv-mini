/*
 * Multi-call X11 + OpenGL/GLX applications for AE350 graphical Linux rootfs:
 *   ae350_wm   - minimalist reparenting window manager + TinyCore slate backdrop
 *   xinfo      - X11 display/visual/XShm probe + XPutImage/XGetImage self-check
 *   glxgears   - 3-gear OpenGL/GLX benchmark with SW-TinyGL and HW-GFX3D support
 *   glxmesh    - 1,000-triangle textured Gouraud 3D mesh OpenGL/GLX benchmark
 */
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <X11/extensions/XShm.h>

#include "GL/gl.h"
#include "GL/glx.h"

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

static uint64_t now_ms(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (uint64_t)ts.tv_sec * 1000ULL + (uint64_t)ts.tv_nsec / 1000000ULL;
}

static unsigned long alloc_rgb_pixel(Display *dpy, int scr,
				     unsigned r8, unsigned g8, unsigned b8)
{
	XColor c;
	Colormap cmap = DefaultColormap(dpy, scr);

	memset(&c, 0, sizeof(c));
	c.red = (unsigned short)(r8 * 257u);
	c.green = (unsigned short)(g8 * 257u);
	c.blue = (unsigned short)(b8 * 257u);
	c.flags = DoRed | DoGreen | DoBlue;
	if (XAllocColor(dpy, cmap, &c))
		return c.pixel;
	return BlackPixel(dpy, scr);
}

/* Verify window framebuffer pixels via XGetImage: returns distinct-color count
 * (capped at 64) and writes FNV-1a checksum to *out_crc. */
static int verify_window_pixels(Display *dpy, Window win, int w, int h,
				uint32_t *out_crc)
{
	XImage *img = XGetImage(dpy, win, 0, 0, (unsigned)w, (unsigned)h,
				AllPlanes, ZPixmap);
	unsigned long seen[64];
	int nseen = 0;
	uint32_t hval = 2166136261u;
	int y, x;

	if (!img) {
		*out_crc = 0u;
		return 0;
	}
	for (y = 0; y < h; y += 2) {
		for (x = 0; x < w; x += 2) {
			unsigned long px = XGetPixel(img, x, y);
			int i, found = 0;

			hval ^= (uint32_t)px;
			hval *= 16777619u;
			for (i = 0; i < nseen; i++) {
				if (seen[i] == px) {
					found = 1;
					break;
				}
			}
			if (!found && nseen < 64)
				seen[nseen++] = px;
		}
	}
	XDestroyImage(img);
	*out_crc = hval;
	return nseen;
}

/* =========================== ae350_wm =========================== */

#define WM_TITLE_H 18
#define WM_BORDER  2
#define WM_MAX_CLIENTS 32

typedef struct {
	Window client;
	Window frame;
	int x, y, w, h;
	char title[64];
} WmClient;

static WmClient g_clients[WM_MAX_CLIENTS];
static volatile int g_wm_conflict = 0;

static int wm_err_handler(Display *dpy, XErrorEvent *ev)
{
	(void)dpy;
	if (ev->error_code == BadAccess)
		g_wm_conflict = 1;
	return 0;
}

static WmClient *wm_find_by_frame(Window f)
{
	for (int i = 0; i < WM_MAX_CLIENTS; i++)
		if (g_clients[i].frame == f && f != None)
			return &g_clients[i];
	return NULL;
}

static WmClient *wm_find_by_client(Window c)
{
	for (int i = 0; i < WM_MAX_CLIENTS; i++)
		if (g_clients[i].client == c && c != None)
			return &g_clients[i];
	return NULL;
}

static void wm_draw_frame(Display *dpy, GC gc, unsigned long fg_bar,
			  unsigned long fg_txt, const WmClient *wc)
{
	XSetForeground(dpy, gc, fg_bar);
	XFillRectangle(dpy, wc->frame, gc, 0, 0,
		       (unsigned)wc->w, WM_TITLE_H);
	XSetForeground(dpy, gc, fg_txt);
	XDrawString(dpy, wc->frame, gc, 6, 13,
		    wc->title, (int)strlen(wc->title));
}

static int run_wm(void)
{
	Display *dpy = XOpenDisplay(NULL);
	int scr;
	Window root;
	GC gc;
	unsigned long bg_desk, bg_frame, fg_bar, fg_txt;
	int cascade = 24;

	if (!dpy) {
		fprintf(stderr, "ae350_wm: cannot open DISPLAY\n");
		return 1;
	}
	scr = DefaultScreen(dpy);
	root = RootWindow(dpy, scr);
	XSetErrorHandler(wm_err_handler);
	XSelectInput(dpy, root, SubstructureRedirectMask | SubstructureNotifyMask | ExposureMask);
	XSync(dpy, False);
	if (g_wm_conflict) {
		fprintf(stderr, "ae350_wm: another WM is active\n");
		return 1;
	}

	bg_desk = alloc_rgb_pixel(dpy, scr, 0x22, 0x38, 0x4D);
	bg_frame = alloc_rgb_pixel(dpy, scr, 0x18, 0x26, 0x35);
	fg_bar = alloc_rgb_pixel(dpy, scr, 0x3A, 0x6E, 0xA5);
	fg_txt = WhitePixel(dpy, scr);
	XSetWindowBackground(dpy, root, bg_desk);
	XClearWindow(dpy, root);
	gc = XCreateGC(dpy, root, 0, NULL);

	printf("AE350_WM READY screen=%dx%d depth=%d\n",
	       DisplayWidth(dpy, scr), DisplayHeight(dpy, scr),
	       DefaultDepth(dpy, scr));
	fflush(stdout);

	for (;;) {
		XEvent ev;
		XNextEvent(dpy, &ev);
		if (ev.type == MapRequest) {
			Window c = ev.xmaprequest.window;
			XWindowAttributes attr;
			WmClient *slot = NULL;
			char *name = NULL;

			if (!XGetWindowAttributes(dpy, c, &attr))
				continue;
			if (attr.override_redirect) {
				XMapWindow(dpy, c);
				continue;
			}
			for (int i = 0; i < WM_MAX_CLIENTS; i++) {
				if (g_clients[i].client == None) {
					slot = &g_clients[i];
					break;
				}
			}
			if (!slot) {
				XMapWindow(dpy, c);
				continue;
			}
			int x = attr.x > 0 ? attr.x : cascade;
			int y = attr.y > 0 ? attr.y : cascade;
			cascade = (cascade + 24) % 96 + 16;
			slot->client = c;
			slot->x = x;
			slot->y = y;
			slot->w = attr.width > 16 ? attr.width : 300;
			slot->h = attr.height > 16 ? attr.height : 240;
			if (XFetchName(dpy, c, &name) && name) {
				snprintf(slot->title, sizeof(slot->title), "%s", name);
				XFree(name);
			} else {
				snprintf(slot->title, sizeof(slot->title), "AE350 App");
			}
			slot->frame = XCreateSimpleWindow(
				dpy, root, x, y,
				(unsigned)slot->w, (unsigned)(slot->h + WM_TITLE_H),
				WM_BORDER, fg_bar, bg_frame);
			XSelectInput(dpy, slot->frame,
				     SubstructureRedirectMask | SubstructureNotifyMask | ExposureMask);
			XAddToSaveSet(dpy, c);
			XReparentWindow(dpy, c, slot->frame, 0, WM_TITLE_H);
			XMapWindow(dpy, c);
			XMapWindow(dpy, slot->frame);
			wm_draw_frame(dpy, gc, fg_bar, fg_txt, slot);
		} else if (ev.type == ConfigureRequest) {
			XConfigureRequestEvent *cr = &ev.xconfigurerequest;
			WmClient *wc = wm_find_by_client(cr->window);
			XWindowChanges ch;
			ch.x = cr->x; ch.y = cr->y;
			ch.width = cr->width; ch.height = cr->height;
			ch.border_width = cr->border_width;
			ch.sibling = cr->above; ch.stack_mode = cr->detail;
			if (wc) {
				if (cr->value_mask & CWWidth) wc->w = cr->width;
				if (cr->value_mask & CWHeight) wc->h = cr->height;
				XResizeWindow(dpy, wc->frame, (unsigned)wc->w,
					      (unsigned)(wc->h + WM_TITLE_H));
				XResizeWindow(dpy, wc->client, (unsigned)wc->w,
					      (unsigned)wc->h);
				wm_draw_frame(dpy, gc, fg_bar, fg_txt, wc);
			} else {
				XConfigureWindow(dpy, cr->window, (unsigned)cr->value_mask, &ch);
			}
		} else if (ev.type == Expose) {
			WmClient *wc = wm_find_by_frame(ev.xexpose.window);
			if (wc)
				wm_draw_frame(dpy, gc, fg_bar, fg_txt, wc);
		} else if (ev.type == UnmapNotify || ev.type == DestroyNotify) {
			Window w = (ev.type == UnmapNotify) ? ev.xunmap.window : ev.xdestroywindow.window;
			WmClient *wc = wm_find_by_client(w);
			if (wc) {
				if (wc->frame != None)
					XDestroyWindow(dpy, wc->frame);
				memset(wc, 0, sizeof(*wc));
			}
		}
	}
}

/* =========================== xinfo =========================== */

static int run_xinfo(void)
{
	Display *dpy = XOpenDisplay(NULL);
	int scr, next = 0, i;
	char **exts;
	Window root, win;
	GC gc;
	uint32_t crc = 0;
	int colors;
	XVisualInfo *vi;
	int glx_attrs[] = { GLX_RGBA, GLX_DOUBLEBUFFER, GLX_DEPTH_SIZE, 16, None };

	if (!dpy) {
		fprintf(stderr, "xinfo: cannot open DISPLAY\n");
		return 1;
	}
	scr = DefaultScreen(dpy);
	root = RootWindow(dpy, scr);
	printf("Display: %s\n", DisplayString(dpy));
	printf("Vendor: %s (release %d)\n", ServerVendor(dpy), VendorRelease(dpy));
	printf("Screen0: %dx%d depth=%d root_visual=0x%lx shm=%d\n",
	       DisplayWidth(dpy, scr), DisplayHeight(dpy, scr),
	       DefaultDepth(dpy, scr),
	       (unsigned long)DefaultVisual(dpy, scr)->visualid,
	       XShmQueryExtension(dpy) ? 1 : 0);

	exts = XListExtensions(dpy, &next);
	printf("Extensions (%d):", next);
	for (i = 0; i < next && i < 12; i++)
		printf(" %s", exts[i]);
	printf("%s\n", next > 12 ? " ..." : "");
	if (exts)
		XFreeExtensionList(exts);

	vi = glXChooseVisual(dpy, scr, glx_attrs);
	if (!vi) {
		fprintf(stderr, "xinfo: glXChooseVisual failed\n");
		XCloseDisplay(dpy);
		return 1;
	}
	printf("GLX visual 0x%lx depth=%d masks=%lx/%lx/%lx\n",
	       (unsigned long)vi->visualid, vi->depth,
	       vi->red_mask, vi->green_mask, vi->blue_mask);
	XFree(vi);

	win = XCreateSimpleWindow(dpy, root, 32, 32, 160, 120, 2,
				  WhitePixel(dpy, scr), BlackPixel(dpy, scr));
	XStoreName(dpy, win, "xinfo probe");
	XMapRaised(dpy, win);
	XSync(dpy, False);
	usleep(50000);

	gc = XCreateGC(dpy, win, 0, NULL);
	XSetForeground(dpy, gc, alloc_rgb_pixel(dpy, scr, 240, 40, 40));
	XFillRectangle(dpy, win, gc, 0, 0, 54, 120);
	XSetForeground(dpy, gc, alloc_rgb_pixel(dpy, scr, 40, 240, 40));
	XFillRectangle(dpy, win, gc, 54, 0, 53, 120);
	XSetForeground(dpy, gc, alloc_rgb_pixel(dpy, scr, 40, 80, 240));
	XFillRectangle(dpy, win, gc, 107, 0, 53, 120);
	XSetForeground(dpy, gc, WhitePixel(dpy, scr));
	XDrawString(dpy, win, gc, 20, 64, "AE350 X11 OK", 12);
	XSync(dpy, False);

	colors = verify_window_pixels(dpy, win, 160, 120, &crc);
	XFreeGC(dpy, gc);
	XDestroyWindow(dpy, win);
	XCloseDisplay(dpy);
	if (colors < 3 || crc == 0u) {
		fprintf(stderr, "xinfo: pixel verification failed (colors=%d crc=%08x)\n",
			colors, crc);
		return 1;
	}
	printf("XINFO PASS colors=%d crc=%08x\n", colors, crc);
	return 0;
}

/* =========================== glxgears =========================== */

static void draw_gear(GLfloat inner_radius, GLfloat outer_radius, GLfloat width,
		      GLint teeth, GLfloat tooth_depth)
{
	GLint i;
	GLfloat r0 = inner_radius;
	GLfloat r1 = outer_radius - tooth_depth / 2.0f;
	GLfloat r2 = outer_radius + tooth_depth / 2.0f;
	GLfloat da = 2.0f * (GLfloat)M_PI / (GLfloat)teeth / 4.0f;
	GLfloat angle;

	glShadeModel(GL_FLAT);
	glNormal3f(0.0f, 0.0f, 1.0f);
	glBegin(GL_QUAD_STRIP);
	for (i = 0; i <= teeth; i++) {
		angle = (GLfloat)i * 2.0f * (GLfloat)M_PI / (GLfloat)teeth;
		glVertex3f(r0 * cosf(angle), r0 * sinf(angle), width * 0.5f);
		glVertex3f(r1 * cosf(angle), r1 * sinf(angle), width * 0.5f);
		if (i < teeth) {
			glVertex3f(r0 * cosf(angle), r0 * sinf(angle), width * 0.5f);
			glVertex3f(r1 * cosf(angle + 3 * da), r1 * sinf(angle + 3 * da), width * 0.5f);
		}
	}
	glEnd();

	glBegin(GL_QUADS);
	for (i = 0; i < teeth; i++) {
		angle = (GLfloat)i * 2.0f * (GLfloat)M_PI / (GLfloat)teeth;
		glVertex3f(r1 * cosf(angle), r1 * sinf(angle), width * 0.5f);
		glVertex3f(r2 * cosf(angle + da), r2 * sinf(angle + da), width * 0.5f);
		glVertex3f(r2 * cosf(angle + 2 * da), r2 * sinf(angle + 2 * da), width * 0.5f);
		glVertex3f(r1 * cosf(angle + 3 * da), r1 * sinf(angle + 3 * da), width * 0.5f);
	}
	glEnd();

	glNormal3f(0.0f, 0.0f, -1.0f);
	glBegin(GL_QUAD_STRIP);
	for (i = 0; i <= teeth; i++) {
		angle = (GLfloat)i * 2.0f * (GLfloat)M_PI / (GLfloat)teeth;
		glVertex3f(r1 * cosf(angle), r1 * sinf(angle), -width * 0.5f);
		glVertex3f(r0 * cosf(angle), r0 * sinf(angle), -width * 0.5f);
		if (i < teeth) {
			glVertex3f(r1 * cosf(angle + 3 * da), r1 * sinf(angle + 3 * da), -width * 0.5f);
			glVertex3f(r0 * cosf(angle), r0 * sinf(angle), -width * 0.5f);
		}
	}
	glEnd();

	glBegin(GL_QUADS);
	for (i = 0; i < teeth; i++) {
		angle = (GLfloat)i * 2.0f * (GLfloat)M_PI / (GLfloat)teeth;
		glVertex3f(r1 * cosf(angle + 3 * da), r1 * sinf(angle + 3 * da), -width * 0.5f);
		glVertex3f(r2 * cosf(angle + 2 * da), r2 * sinf(angle + 2 * da), -width * 0.5f);
		glVertex3f(r2 * cosf(angle + da), r2 * sinf(angle + da), -width * 0.5f);
		glVertex3f(r1 * cosf(angle), r1 * sinf(angle), -width * 0.5f);
	}
	glEnd();

	glBegin(GL_QUAD_STRIP);
	for (i = 0; i < teeth; i++) {
		GLfloat u, v, len;
		angle = (GLfloat)i * 2.0f * (GLfloat)M_PI / (GLfloat)teeth;
		glVertex3f(r1 * cosf(angle), r1 * sinf(angle), width * 0.5f);
		glVertex3f(r1 * cosf(angle), r1 * sinf(angle), -width * 0.5f);
		u = r2 * cosf(angle + da) - r1 * cosf(angle);
		v = r2 * sinf(angle + da) - r1 * sinf(angle);
		len = sqrtf(u * u + v * v);
		u /= len; v /= len;
		glNormal3f(v, -u, 0.0f);
		glVertex3f(r2 * cosf(angle + da), r2 * sinf(angle + da), width * 0.5f);
		glVertex3f(r2 * cosf(angle + da), r2 * sinf(angle + da), -width * 0.5f);
		glNormal3f(cosf(angle), sinf(angle), 0.0f);
		glVertex3f(r2 * cosf(angle + 2 * da), r2 * sinf(angle + 2 * da), width * 0.5f);
		glVertex3f(r2 * cosf(angle + 2 * da), r2 * sinf(angle + 2 * da), -width * 0.5f);
		u = r1 * cosf(angle + 3 * da) - r2 * cosf(angle + 2 * da);
		v = r1 * sinf(angle + 3 * da) - r2 * sinf(angle + 2 * da);
		glNormal3f(v, -u, 0.0f);
		glVertex3f(r1 * cosf(angle + 3 * da), r1 * sinf(angle + 3 * da), width * 0.5f);
		glVertex3f(r1 * cosf(angle + 3 * da), r1 * sinf(angle + 3 * da), -width * 0.5f);
		glNormal3f(cosf(angle), sinf(angle), 0.0f);
	}
	glVertex3f(r1 * cosf(0), r1 * sinf(0), width * 0.5f);
	glVertex3f(r1 * cosf(0), r1 * sinf(0), -width * 0.5f);
	glEnd();

	glShadeModel(GL_SMOOTH);
	glBegin(GL_QUAD_STRIP);
	for (i = 0; i <= teeth; i++) {
		angle = (GLfloat)i * 2.0f * (GLfloat)M_PI / (GLfloat)teeth;
		glNormal3f(-cosf(angle), -sinf(angle), 0.0f);
		glVertex3f(r0 * cosf(angle), r0 * sinf(angle), -width * 0.5f);
		glVertex3f(r0 * cosf(angle), r0 * sinf(angle), width * 0.5f);
	}
	glEnd();
}

static int run_glxgears(int argc, char **argv)
{
	int frames = 30, w = 300, h = 300;
	Display *dpy;
	int scr;
	XVisualInfo *vi;
	Window win;
	GLXContext ctx;
	int attrs[] = { GLX_RGBA, GLX_DOUBLEBUFFER, GLX_DEPTH_SIZE, 16, None };
	static const GLfloat light_pos[4] = { 5.0f, 5.0f, 10.0f, 0.0f };
	static const GLfloat red[4]   = { 0.85f, 0.15f, 0.10f, 1.0f };
	static const GLfloat green[4] = { 0.15f, 0.85f, 0.25f, 1.0f };
	static const GLfloat blue[4]  = { 0.20f, 0.35f, 0.95f, 1.0f };
	GLfloat angle = 0.0f;
	uint64_t t0;
	int f;

	for (int i = 1; i < argc; i++) {
		if (strcmp(argv[i], "--frames") == 0 && i + 1 < argc)
			frames = atoi(argv[++i]);
		else if (strcmp(argv[i], "--size") == 0 && i + 1 < argc)
			sscanf(argv[++i], "%dx%d", &w, &h);
	}

	dpy = XOpenDisplay(NULL);
	if (!dpy) {
		fprintf(stderr, "glxgears: cannot open DISPLAY\n");
		return 1;
	}
	scr = DefaultScreen(dpy);
	vi = glXChooseVisual(dpy, scr, attrs);
	if (!vi) {
		fprintf(stderr, "glxgears: no suitable GLX visual\n");
		return 1;
	}
	win = XCreateSimpleWindow(dpy, RootWindow(dpy, scr), 40, 40,
				  (unsigned)w, (unsigned)h, 2,
				  WhitePixel(dpy, scr), BlackPixel(dpy, scr));
	XStoreName(dpy, win, "glxgears (TinyGL)");
	XMapRaised(dpy, win);
	XSync(dpy, False);

	ctx = glXCreateContext(dpy, vi, NULL, True);
	XFree(vi);
	if (!ctx || !glXMakeCurrent(dpy, win, ctx)) {
		fprintf(stderr, "glxgears: glXMakeCurrent failed\n");
		return 1;
	}

	glViewport(0, 0, w, h);
	glMatrixMode(GL_PROJECTION);
	glLoadIdentity();
	GLfloat ar = (GLfloat)h / (GLfloat)w;
	glFrustum(-1.0, 1.0, -ar, ar, 5.0, 60.0);
	glMatrixMode(GL_MODELVIEW);
	glLoadIdentity();
	glTranslatef(0.0f, 0.0f, -40.0f);

	glClearColor(0.04f, 0.06f, 0.12f, 1.0f);
	glEnable(GL_CULL_FACE);
	glEnable(GL_LIGHTING);
	glEnable(GL_LIGHT0);
	glEnable(GL_DEPTH_TEST);
	glEnable(GL_NORMALIZE);
	glLightfv(GL_LIGHT0, GL_POSITION, light_pos);

	t0 = now_ms();
	for (f = 0; frames == 0 || f < frames; f++) {
		angle = (GLfloat)f * 3.0f;
		glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT);

		glPushMatrix();
		glRotatef(20.0f, 1.0f, 0.0f, 0.0f);
		glRotatef(30.0f, 0.0f, 1.0f, 0.0f);

		glPushMatrix();
		glTranslatef(-3.0f, -2.0f, 0.0f);
		glRotatef(angle, 0.0f, 0.0f, 1.0f);
		glMaterialfv(GL_FRONT, GL_AMBIENT_AND_DIFFUSE, red);
		draw_gear(1.0f, 4.0f, 1.0f, 20, 0.7f);
		glPopMatrix();

		glPushMatrix();
		glTranslatef(3.1f, -2.0f, 0.0f);
		glRotatef(-2.0f * angle - 9.0f, 0.0f, 0.0f, 1.0f);
		glMaterialfv(GL_FRONT, GL_AMBIENT_AND_DIFFUSE, green);
		draw_gear(0.5f, 2.0f, 2.0f, 10, 0.7f);
		glPopMatrix();

		glPushMatrix();
		glTranslatef(-3.1f, 4.2f, 0.0f);
		glRotatef(-2.0f * angle - 25.0f, 0.0f, 0.0f, 1.0f);
		glMaterialfv(GL_FRONT, GL_AMBIENT_AND_DIFFUSE, blue);
		draw_gear(1.3f, 2.0f, 0.5f, 10, 0.7f);
		glPopMatrix();

		glPopMatrix();
		glXSwapBuffers(dpy, win);
	}

	uint64_t dt = now_ms() - t0;
	if (dt == 0) dt = 1;
	double fps = (double)f * 1000.0 / (double)dt;
	uint32_t crc = 0;
	int colors = verify_window_pixels(dpy, win, w, h, &crc);
	const char *backend = ae350_glx_backend_name();
	unsigned long hw_px = ae350_glx_last_pixels_out();

	glXMakeCurrent(dpy, 0, NULL);
	glXDestroyContext(dpy, ctx);
	XDestroyWindow(dpy, win);
	XCloseDisplay(dpy);

	if (colors < 8 || crc == 0u) {
		fprintf(stderr, "glxgears: rendered frame looks blank (colors=%d crc=%08x)\n",
			colors, crc);
		return 1;
	}
	printf("GLXGEARS PASS backend=%s frames=%d elapsed_ms=%llu fps=%.2f crc=%08x colors=%d hw_pixels=%lu\n",
	       backend, f, (unsigned long long)dt, fps, crc, colors, hw_px);
	return 0;
}

/* =========================== glxmesh =========================== */

#define MESH_COLS 20
#define MESH_ROWS 25

typedef struct {
	float x, y, z;
	float nx, ny, nz;
	float u, v;
} MeshVert;

static MeshVert g_mesh[(MESH_ROWS + 1) * (MESH_COLS + 1)];
static uint16_t g_tex[64 * 64];

static void build_mesh_texture(void)
{
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

static void build_wave_mesh(void)
{
	for (int j = 0; j <= MESH_ROWS; j++) {
		float v = (float)j / (float)MESH_ROWS;
		float y = (v - 0.5f) * 3.0f;
		for (int i = 0; i <= MESH_COLS; i++) {
			float u = (float)i / (float)MESH_COLS;
			float x = (u - 0.5f) * 3.6f;
			float r = sqrtf(x * x + y * y);
			float z = 0.42f * cosf(r * 2.5f) * expf(-0.18f * r * r);
			float dr = -0.42f * (2.5f * sinf(r * 2.5f) + 0.36f * r * cosf(r * 2.5f)) *
				   expf(-0.18f * r * r);
			float dzdx = (r > 1e-4f) ? (dr * x / r) : 0.0f;
			float dzdy = (r > 1e-4f) ? (dr * y / r) : 0.0f;
			float nx = -dzdx, ny = -dzdy, nz = 1.0f;
			float inv = 1.0f / sqrtf(nx * nx + ny * ny + nz * nz);
			MeshVert *mv = &g_mesh[j * (MESH_COLS + 1) + i];
			mv->x = x; mv->y = y; mv->z = z;
			mv->nx = nx * inv; mv->ny = ny * inv; mv->nz = nz * inv;
			mv->u = u * 2.0f; mv->v = v * 2.0f;
		}
	}
}

static int run_glxmesh(int argc, char **argv)
{
	int frames = 20, w = 320, h = 240;
	Display *dpy;
	int scr;
	XVisualInfo *vi;
	Window win;
	GLXContext ctx;
	int attrs[] = { GLX_RGBA, GLX_DOUBLEBUFFER, GLX_DEPTH_SIZE, 16, None };
	static const GLfloat light_pos[4] = { 1.5f, 2.0f, 3.0f, 0.0f };
	static const GLfloat white[4]     = { 0.95f, 0.95f, 0.95f, 1.0f };
	GLuint tex_id = 0;
	uint64_t t0;
	int f;

	for (int i = 1; i < argc; i++) {
		if (strcmp(argv[i], "--frames") == 0 && i + 1 < argc)
			frames = atoi(argv[++i]);
		else if (strcmp(argv[i], "--size") == 0 && i + 1 < argc)
			sscanf(argv[++i], "%dx%d", &w, &h);
	}

	build_mesh_texture();
	build_wave_mesh();

	dpy = XOpenDisplay(NULL);
	if (!dpy) {
		fprintf(stderr, "glxmesh: cannot open DISPLAY\n");
		return 1;
	}
	scr = DefaultScreen(dpy);
	vi = glXChooseVisual(dpy, scr, attrs);
	if (!vi) {
		fprintf(stderr, "glxmesh: no suitable GLX visual\n");
		return 1;
	}
	win = XCreateSimpleWindow(dpy, RootWindow(dpy, scr), 80, 70,
				  (unsigned)w, (unsigned)h, 2,
				  WhitePixel(dpy, scr), BlackPixel(dpy, scr));
	XStoreName(dpy, win, "glxmesh 1K-tri (TinyGL)");
	XMapRaised(dpy, win);
	XSync(dpy, False);

	ctx = glXCreateContext(dpy, vi, NULL, True);
	XFree(vi);
	if (!ctx || !glXMakeCurrent(dpy, win, ctx)) {
		fprintf(stderr, "glxmesh: glXMakeCurrent failed\n");
		return 1;
	}

	glViewport(0, 0, w, h);
	glMatrixMode(GL_PROJECTION);
	glLoadIdentity();
	gluPerspective(45.0, (double)w / (double)h, 0.5, 20.0);
	glMatrixMode(GL_MODELVIEW);
	glClearColor(0.08f, 0.12f, 0.20f, 1.0f);
	glEnable(GL_DEPTH_TEST);
	glEnable(GL_LIGHTING);
	glEnable(GL_LIGHT0);
	glLightfv(GL_LIGHT0, GL_POSITION, light_pos);
	glMaterialfv(GL_FRONT, GL_DIFFUSE, white);
	glShadeModel(GL_SMOOTH);
	glEnable(GL_TEXTURE_2D);
	glGenTextures(1, &tex_id);
	glBindTexture(GL_TEXTURE_2D, tex_id);
	glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB, 64, 64, 0,
		     GL_RGB, GL_UNSIGNED_SHORT_5_6_5, g_tex);
	glTexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE);

	t0 = now_ms();
	for (f = 0; frames == 0 || f < frames; f++) {
		glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT);
		glLoadIdentity();
		glTranslatef(0.0f, 0.0f, -4.4f);
		glRotatef(38.0f, 1.0f, 0.0f, 0.0f);
		glRotatef((GLfloat)f * 4.5f, 0.0f, 0.0f, 1.0f);

		glBegin(GL_TRIANGLES);
		for (int j = 0; j < MESH_ROWS; j++) {
			for (int i = 0; i < MESH_COLS; i++) {
				const MeshVert *v00 = &g_mesh[j * (MESH_COLS + 1) + i];
				const MeshVert *v10 = &g_mesh[j * (MESH_COLS + 1) + i + 1];
				const MeshVert *v01 = &g_mesh[(j + 1) * (MESH_COLS + 1) + i];
				const MeshVert *v11 = &g_mesh[(j + 1) * (MESH_COLS + 1) + i + 1];

				glTexCoord2f(v00->u, v00->v); glNormal3f(v00->nx, v00->ny, v00->nz); glVertex3f(v00->x, v00->y, v00->z);
				glTexCoord2f(v10->u, v10->v); glNormal3f(v10->nx, v10->ny, v10->nz); glVertex3f(v10->x, v10->y, v10->z);
				glTexCoord2f(v11->u, v11->v); glNormal3f(v11->nx, v11->ny, v11->nz); glVertex3f(v11->x, v11->y, v11->z);

				glTexCoord2f(v00->u, v00->v); glNormal3f(v00->nx, v00->ny, v00->nz); glVertex3f(v00->x, v00->y, v00->z);
				glTexCoord2f(v11->u, v11->v); glNormal3f(v11->nx, v11->ny, v11->nz); glVertex3f(v11->x, v11->y, v11->z);
				glTexCoord2f(v01->u, v01->v); glNormal3f(v01->nx, v01->ny, v01->nz); glVertex3f(v01->x, v01->y, v01->z);
			}
		}
		glEnd();
		glXSwapBuffers(dpy, win);
	}

	uint64_t dt = now_ms() - t0;
	if (dt == 0) dt = 1;
	double fps = (double)f * 1000.0 / (double)dt;
	uint32_t crc = 0;
	int colors = verify_window_pixels(dpy, win, w, h, &crc);
	const char *backend = ae350_glx_backend_name();
	unsigned long hw_px = ae350_glx_last_pixels_out();

	glXMakeCurrent(dpy, 0, NULL);
	glXDestroyContext(dpy, ctx);
	XDestroyWindow(dpy, win);
	XCloseDisplay(dpy);

	if (colors < 8 || crc == 0u) {
		fprintf(stderr, "glxmesh: rendered frame looks blank (colors=%d crc=%08x)\n",
			colors, crc);
		return 1;
	}
	printf("GLXMESH PASS backend=%s tris=1000 frames=%d elapsed_ms=%llu fps=%.2f crc=%08x colors=%d hw_pixels=%lu\n",
	       backend, f, (unsigned long long)dt, fps, crc, colors, hw_px);
	return 0;
}

static const char *base_name(const char *s)
{
	const char *p = strrchr(s, '/');
	return p ? p + 1 : s;
}

int main(int argc, char **argv)
{
	const char *name = argc > 0 ? base_name(argv[0]) : "ae350-gl-apps";

	if (strcmp(name, "ae350_wm") == 0)
		return run_wm();
	if (strcmp(name, "xinfo") == 0)
		return run_xinfo();
	if (strcmp(name, "glxgears") == 0)
		return run_glxgears(argc, argv);
	if (strcmp(name, "glxmesh") == 0)
		return run_glxmesh(argc, argv);
	if (argc >= 2) {
		if (strcmp(argv[1], "wm") == 0)
			return run_wm();
		if (strcmp(argv[1], "xinfo") == 0)
			return run_xinfo();
		if (strcmp(argv[1], "glxgears") == 0)
			return run_glxgears(argc - 1, argv + 1);
		if (strcmp(argv[1], "glxmesh") == 0)
			return run_glxmesh(argc - 1, argv + 1);
	}
	fprintf(stderr, "Usage: ae350_wm | xinfo | glxgears [--frames N] | glxmesh [--frames N]\n");
	return 1;
}
