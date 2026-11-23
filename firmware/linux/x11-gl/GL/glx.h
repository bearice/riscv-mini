#ifndef _AE350_GL_GLX_H
#define _AE350_GL_GLX_H

#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <GL/gl.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef XID GLXDrawable;
typedef struct __GLXcontextRec *GLXContext;

#define GLX_USE_GL              1
#define GLX_BUFFER_SIZE         2
#define GLX_LEVEL               3
#define GLX_RGBA                4
#define GLX_DOUBLEBUFFER        5
#define GLX_STEREO              6
#define GLX_AUX_BUFFERS         7
#define GLX_RED_SIZE            8
#define GLX_GREEN_SIZE          9
#define GLX_BLUE_SIZE           10
#define GLX_ALPHA_SIZE          11
#define GLX_DEPTH_SIZE          12
#define GLX_STENCIL_SIZE        13

Bool glXQueryVersion(Display *dpy, int *major, int *minor);
const char *glXQueryExtensionsString(Display *dpy, int screen);
XVisualInfo *glXChooseVisual(Display *dpy, int screen, int *attribList);
GLXContext glXCreateContext(Display *dpy, XVisualInfo *vis,
			    GLXContext shareList, Bool direct);
void glXDestroyContext(Display *dpy, GLXContext ctx);
Bool glXMakeCurrent(Display *dpy, GLXDrawable drawable, GLXContext ctx);
void glXSwapBuffers(Display *dpy, GLXDrawable drawable);
void glXWaitGL(void);
void glXWaitX(void);
GLXContext glXGetCurrentContext(void);
GLXDrawable glXGetCurrentDrawable(void);

const char *ae350_glx_backend_name(void);
unsigned long ae350_glx_last_pixels_out(void);

#ifdef __cplusplus
}
#endif

#endif /* _AE350_GL_GLX_H */
