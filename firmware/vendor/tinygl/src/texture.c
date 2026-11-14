#include "zgl.h"

void glGenTextures(GLsizei n, GLuint *textures) {
    GLContext *c = glGetContext();
    int found = 0;
    for (int i = 1; i < TGL_MAX_TEXTURES && found < n; i++) {
        if (!c->textures[i].allocated) {
            c->textures[i].allocated = 1;
            c->textures[i].width = 0;
            c->textures[i].height = 0;
            textures[found++] = (GLuint)i;
        }
    }
}

void glBindTexture(GLenum target, GLuint texture) {
    if (target != GL_TEXTURE_2D) return;
    GLContext *c = glGetContext();
    if (texture < TGL_MAX_TEXTURES) {
        c->current_tex_id = (int)texture;
        GLTexture *t = &c->textures[texture];
        if (t->width > 0 && t->height > 0) {
            ZB_setTexture(c->zb, t->data, t->width, t->height);
        } else {
            ZB_setTexture(c->zb, (const uint16_t *)0, 0, 0);
        }
    }
}

void glTexImage2D(GLenum target, GLint level, GLint internalformat,
                  GLsizei width, GLsizei height, GLint border,
                  GLenum format, GLenum type, const GLvoid *pixels) {
    (void)internalformat;
    (void)border;
    if (target != GL_TEXTURE_2D || level != 0 || !pixels) return;
    if (width <= 0 || height <= 0 || width > TGL_MAX_TEX_DIM || height > TGL_MAX_TEX_DIM) return;
    GLContext *c = glGetContext();
    GLTexture *t = &c->textures[c->current_tex_id];
    t->allocated = 1;
    t->width = width;
    t->height = height;
    int count = width * height;
    if (format == GL_RGB && type == GL_UNSIGNED_SHORT_5_6_5) {
        const uint16_t *src = (const uint16_t *)pixels;
        for (int i = 0; i < count; i++) t->data[i] = src[i];
    } else if (format == GL_RGBA && type == GL_UNSIGNED_BYTE) {
        const uint8_t *src = (const uint8_t *)pixels;
        for (int i = 0; i < count; i++) {
            uint16_t r = src[i * 4 + 0] >> 3;
            uint16_t g = src[i * 4 + 1] >> 2;
            uint16_t b = src[i * 4 + 2] >> 3;
            t->data[i] = (uint16_t)((r << 11) | (g << 5) | b);
        }
    } else if (format == GL_RGB && type == GL_UNSIGNED_BYTE) {
        const uint8_t *src = (const uint8_t *)pixels;
        for (int i = 0; i < count; i++) {
            uint16_t r = src[i * 3 + 0] >> 3;
            uint16_t g = src[i * 3 + 1] >> 2;
            uint16_t b = src[i * 3 + 2] >> 3;
            t->data[i] = (uint16_t)((r << 11) | (g << 5) | b);
        }
    }
    ZB_setTexture(c->zb, t->data, t->width, t->height);
}

void glTexParameteri(GLenum target, GLenum pname, GLint param) {
    (void)target; (void)pname; (void)param;
}

void glTexEnvi(GLenum target, GLenum pname, GLint param) {
    GLContext *c = glGetContext();
    if (target == GL_TEXTURE_ENV && pname == GL_TEXTURE_ENV_MODE) {
        c->tex_env_mode = param;
    }
}
