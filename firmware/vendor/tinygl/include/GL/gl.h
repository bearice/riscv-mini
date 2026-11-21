#ifndef TGL_GL_H
#define TGL_GL_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef unsigned int    GLenum;
typedef unsigned char   GLboolean;
typedef unsigned int    GLbitfield;
typedef signed char     GLbyte;
typedef short           GLshort;
typedef int             GLint;
typedef int             GLsizei;
typedef unsigned char   GLubyte;
typedef unsigned short  GLushort;
typedef unsigned int    GLuint;
typedef float           GLfloat;
typedef float           GLclampf;
typedef double          GLdouble;
typedef double          GLclampd;
typedef void            GLvoid;

#define GL_FALSE                        0
#define GL_TRUE                         1

/* Primitives */
#define GL_POINTS                       0x0000
#define GL_LINES                        0x0001
#define GL_LINE_LOOP                    0x0002
#define GL_LINE_STRIP                   0x0003
#define GL_TRIANGLES                    0x0004
#define GL_TRIANGLE_STRIP               0x0005
#define GL_TRIANGLE_FAN                 0x0006
#define GL_QUADS                        0x0007
#define GL_QUAD_STRIP                   0x0008
#define GL_POLYGON                      0x0009

/* Matrix Modes */
#define GL_MODELVIEW                    0x1700
#define GL_PROJECTION                   0x1701
#define GL_TEXTURE                      0x1702

/* Clear bits */
#define GL_DEPTH_BUFFER_BIT             0x00000100
#define GL_COLOR_BUFFER_BIT             0x00004000

/* Enable/Disable capabilities */
#define GL_CULL_FACE                    0x0B44
#define GL_LIGHTING                     0x0B50
#define GL_DEPTH_TEST                   0x0B71
#define GL_NORMALIZE                    0x0BA1
#define GL_TEXTURE_2D                   0x0DE1
#define GL_COLOR_MATERIAL               0x0B57
#define GL_LIGHT0                       0x4000
#define GL_LIGHT1                       0x4001
#define GL_LIGHT2                       0x4002
#define GL_LIGHT3                       0x4003

/* Shading model */
#define GL_FLAT                         0x1D00
#define GL_SMOOTH                       0x1D01

/* Cull face / Front face */
#define GL_FRONT                        0x0404
#define GL_BACK                         0x0405
#define GL_FRONT_AND_BACK               0x0408
#define GL_CW                           0x0900
#define GL_CCW                          0x0901

/* Depth funcs */
#define GL_NEVER                        0x0200
#define GL_LESS                         0x0201
#define GL_EQUAL                        0x0202
#define GL_LEQUAL                       0x0203
#define GL_GREATER                      0x0204
#define GL_NOTEQUAL                     0x0205
#define GL_GEQUAL                       0x0206
#define GL_ALWAYS                       0x0207

/* Light & Material parameters */
#define GL_AMBIENT                      0x1200
#define GL_DIFFUSE                      0x1201
#define GL_SPECULAR                     0x1202
#define GL_POSITION                     0x1203
#define GL_EMISSION                     0x1600
#define GL_SHININESS                    0x1601
#define GL_AMBIENT_AND_DIFFUSE          0x1602
#define GL_LIGHT_MODEL_AMBIENT          0x0B53

/* Texture formats & params */
#define GL_RGB                          0x1907
#define GL_RGBA                         0x1908
#define GL_UNSIGNED_BYTE                0x1401
#define GL_UNSIGNED_SHORT_5_6_5         0x8363
#define GL_TEXTURE_MAG_FILTER           0x2800
#define GL_TEXTURE_MIN_FILTER           0x2801
#define GL_TEXTURE_WRAP_S               0x2802
#define GL_TEXTURE_WRAP_T               0x2803
#define GL_NEAREST                      0x2600
#define GL_LINEAR                       0x2601
#define GL_REPEAT                       0x2901
#define GL_CLAMP                        0x2900
#define GL_TEXTURE_ENV                  0x2300
#define GL_TEXTURE_ENV_MODE             0x2200
#define GL_MODULATE                     0x2100
#define GL_REPLACE                      0x1E01

void glClearColor(GLclampf red, GLclampf green, GLclampf blue, GLclampf alpha);
void glClear(GLbitfield mask);
void glViewport(GLint x, GLint y, GLsizei width, GLsizei height);

void glMatrixMode(GLenum mode);
void glLoadIdentity(void);
void glLoadMatrixf(const GLfloat *m);
void glMultMatrixf(const GLfloat *m);
void glPushMatrix(void);
void glPopMatrix(void);
void glTranslatef(GLfloat x, GLfloat y, GLfloat z);
void glRotatef(GLfloat angle, GLfloat x, GLfloat y, GLfloat z);
void glScalef(GLfloat x, GLfloat y, GLfloat z);
void glFrustum(GLdouble left, GLdouble right, GLdouble bottom, GLdouble top,
               GLdouble zNear, GLdouble zFar);
void glOrtho(GLdouble left, GLdouble right, GLdouble bottom, GLdouble top,
             GLdouble zNear, GLdouble zFar);
void gluPerspective(GLdouble fovy, GLdouble aspect, GLdouble zNear, GLdouble zFar);

void glEnable(GLenum cap);
void glDisable(GLenum cap);
void glCullFace(GLenum mode);
void glFrontFace(GLenum mode);
void glDepthFunc(GLenum func);
void glDepthMask(GLboolean flag);
void glShadeModel(GLenum mode);

void glLightfv(GLenum light, GLenum pname, const GLfloat *params);
void glLightModelfv(GLenum pname, const GLfloat *params);
void glMaterialfv(GLenum face, GLenum pname, const GLfloat *params);
void glMaterialf(GLenum face, GLenum pname, GLfloat param);

void glGenTextures(GLsizei n, GLuint *textures);
void glBindTexture(GLenum target, GLuint texture);
void glTexImage2D(GLenum target, GLint level, GLint internalformat,
                  GLsizei width, GLsizei height, GLint border,
                  GLenum format, GLenum type, const GLvoid *pixels);
void glTexParameteri(GLenum target, GLenum pname, GLint param);
void glTexEnvi(GLenum target, GLenum pname, GLint param);

void glBegin(GLenum mode);
void glEnd(void);
void glVertex3f(GLfloat x, GLfloat y, GLfloat z);
void glVertex3fv(const GLfloat *v);
void glNormal3f(GLfloat nx, GLfloat ny, GLfloat nz);
void glNormal3fv(const GLfloat *v);
void glColor3f(GLfloat red, GLfloat green, GLfloat blue);
void glColor4f(GLfloat red, GLfloat green, GLfloat blue, GLfloat alpha);
void glColor3ub(GLubyte red, GLubyte green, GLubyte blue);
void glTexCoord2f(GLfloat s, GLfloat t);

void glFlush(void);
void glFinish(void);

#ifdef __cplusplus
}
#endif

#endif /* TGL_GL_H */
