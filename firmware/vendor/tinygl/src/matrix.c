#include "zgl.h"

static int matrix_mode_idx(GLenum mode) {
    switch (mode) {
    case GL_MODELVIEW:  return 0;
    case GL_PROJECTION: return 1;
    case GL_TEXTURE:    return 2;
    default:            return 0;
    }
}

static M4 *current_matrix(GLContext *c) {
    int m = c->matrix_mode;
    return &c->matrix_stack[m][c->matrix_stack_ptr[m]];
}

static void mark_dirty(GLContext *c) {
    if (c->matrix_mode == 0) {
        c->matrix_model_projection_updated = 1;
        c->matrix_model_view_inv_updated = 1;
    } else if (c->matrix_mode == 1) {
        c->matrix_model_projection_updated = 1;
    }
}

void glMatrixMode(GLenum mode) {
    GLContext *c = glGetContext();
    c->matrix_mode = matrix_mode_idx(mode);
}

void glLoadIdentity(void) {
    GLContext *c = glGetContext();
    gl_M4_Id(current_matrix(c));
    mark_dirty(c);
}

void glLoadMatrixf(const GLfloat *m) {
    GLContext *c = glGetContext();
    M4 *dst = current_matrix(c);
    for (int col = 0; col < 4; col++)
        for (int row = 0; row < 4; row++)
            dst->m[row][col] = m[col * 4 + row];
    mark_dirty(c);
}

void glMultMatrixf(const GLfloat *m) {
    GLContext *c = glGetContext();
    M4 mat;
    for (int col = 0; col < 4; col++)
        for (int row = 0; row < 4; row++)
            mat.m[row][col] = m[col * 4 + row];
    gl_M4_MulLeft(current_matrix(c), &mat);
    mark_dirty(c);
}

void glPushMatrix(void) {
    GLContext *c = glGetContext();
    int m = c->matrix_mode;
    int sp = c->matrix_stack_ptr[m];
    if (sp + 1 < TGL_MATRIX_STACK_DEPTH) {
        c->matrix_stack[m][sp + 1] = c->matrix_stack[m][sp];
        c->matrix_stack_ptr[m] = sp + 1;
    }
}

void glPopMatrix(void) {
    GLContext *c = glGetContext();
    int m = c->matrix_mode;
    if (c->matrix_stack_ptr[m] > 0) {
        c->matrix_stack_ptr[m]--;
        mark_dirty(c);
    }
}

void glTranslatef(GLfloat x, GLfloat y, GLfloat z) {
    GLContext *c = glGetContext();
    M4 *m = current_matrix(c);
    for (int i = 0; i < 4; i++) {
        m->m[i][3] += m->m[i][0] * x + m->m[i][1] * y + m->m[i][2] * z;
    }
    mark_dirty(c);
}

void glScalef(GLfloat x, GLfloat y, GLfloat z) {
    GLContext *c = glGetContext();
    M4 *m = current_matrix(c);
    for (int i = 0; i < 4; i++) {
        m->m[i][0] *= x;
        m->m[i][1] *= y;
        m->m[i][2] *= z;
    }
    mark_dirty(c);
}

void glRotatef(GLfloat angle, GLfloat x, GLfloat y, GLfloat z) {
    GLContext *c = glGetContext();
    float rad = angle * (3.14159265358979323846f / 180.0f);
    float len = tgl_sqrtf(x*x + y*y + z*z);
    if (len == 0.0f) return;
    float inv = 1.0f / len;
    x *= inv; y *= inv; z *= inv;
    float s = tgl_sinf(rad);
    float co = tgl_cosf(rad);
    float oc = 1.0f - co;

    M4 r;
    gl_M4_Id(&r);
    r.m[0][0] = x*x*oc + co;     r.m[0][1] = x*y*oc - z*s; r.m[0][2] = x*z*oc + y*s;
    r.m[1][0] = y*x*oc + z*s;    r.m[1][1] = y*y*oc + co;  r.m[1][2] = y*z*oc - x*s;
    r.m[2][0] = z*x*oc - y*s;    r.m[2][1] = z*y*oc + x*s; r.m[2][2] = z*z*oc + co;

    gl_M4_MulLeft(current_matrix(c), &r);
    mark_dirty(c);
}

void glFrustum(GLdouble left, GLdouble right, GLdouble bottom, GLdouble top,
               GLdouble zNear, GLdouble zFar) {
    GLContext *c = glGetContext();
    float l = (float)left, r = (float)right, b = (float)bottom, t = (float)top;
    float n = (float)zNear, f = (float)zFar;
    M4 m;
    gl_M4_Id(&m);
    m.m[0][0] = (2.0f * n) / (r - l);
    m.m[0][2] = (r + l) / (r - l);
    m.m[1][1] = (2.0f * n) / (t - b);
    m.m[1][2] = (t + b) / (t - b);
    m.m[2][2] = -(f + n) / (f - n);
    m.m[2][3] = -(2.0f * f * n) / (f - n);
    m.m[3][2] = -1.0f;
    m.m[3][3] = 0.0f;
    gl_M4_MulLeft(current_matrix(c), &m);
    mark_dirty(c);
}

void glOrtho(GLdouble left, GLdouble right, GLdouble bottom, GLdouble top,
             GLdouble zNear, GLdouble zFar) {
    GLContext *c = glGetContext();
    float l = (float)left, r = (float)right, b = (float)bottom, t = (float)top;
    float n = (float)zNear, f = (float)zFar;
    M4 m;
    gl_M4_Id(&m);
    m.m[0][0] = 2.0f / (r - l);
    m.m[0][3] = -(r + l) / (r - l);
    m.m[1][1] = 2.0f / (t - b);
    m.m[1][3] = -(t + b) / (t - b);
    m.m[2][2] = -2.0f / (f - n);
    m.m[2][3] = -(f + n) / (f - n);
    gl_M4_MulLeft(current_matrix(c), &m);
    mark_dirty(c);
}

void gluPerspective(GLdouble fovy, GLdouble aspect, GLdouble zNear, GLdouble zFar) {
    float rad = (float)fovy * (3.14159265358979323846f / 360.0f);
    float s = tgl_sinf(rad);
    float c = tgl_cosf(rad);
    float top = (float)zNear * (s / c);
    float bottom = -top;
    float right = top * (float)aspect;
    float left = -right;
    glFrustum(left, right, bottom, top, zNear, zFar);
}
