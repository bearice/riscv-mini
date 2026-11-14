#include "zgl.h"

static inline float clampf01(float x) {
    if (x < 0.0f) return 0.0f;
    if (x > 1.0f) return 1.0f;
    return x;
}

void glLightfv(GLenum light, GLenum pname, const GLfloat *params) {
    GLContext *c = glGetContext();
    int idx = (int)(light - GL_LIGHT0);
    if (idx < 0 || idx >= TGL_MAX_LIGHTS) return;
    GLLight *l = &c->lights[idx];
    switch (pname) {
    case GL_AMBIENT:
        for (int i = 0; i < 4; i++) l->ambient.v[i] = params[i];
        break;
    case GL_DIFFUSE:
        for (int i = 0; i < 4; i++) l->diffuse.v[i] = params[i];
        break;
    case GL_SPECULAR:
        for (int i = 0; i < 4; i++) l->specular.v[i] = params[i];
        break;
    case GL_POSITION: {
        V4 p = {{ params[0], params[1], params[2], params[3] }};
        const M4 *mv = &c->matrix_stack[0][c->matrix_stack_ptr[0]];
        gl_M4_MulV4(&l->position, mv, &p);
        break;
    }
    default:
        break;
    }
}

void glLightModelfv(GLenum pname, const GLfloat *params) {
    GLContext *c = glGetContext();
    if (pname == GL_LIGHT_MODEL_AMBIENT) {
        for (int i = 0; i < 4; i++) c->ambient_light_model.v[i] = params[i];
    }
}

void glMaterialfv(GLenum face, GLenum pname, const GLfloat *params) {
    (void)face;
    GLContext *c = glGetContext();
    switch (pname) {
    case GL_AMBIENT:
        for (int i = 0; i < 4; i++) c->material.ambient.v[i] = params[i];
        break;
    case GL_DIFFUSE:
        for (int i = 0; i < 4; i++) c->material.diffuse.v[i] = params[i];
        break;
    case GL_AMBIENT_AND_DIFFUSE:
        for (int i = 0; i < 4; i++) {
            c->material.ambient.v[i] = params[i];
            c->material.diffuse.v[i] = params[i];
        }
        break;
    case GL_SPECULAR:
        for (int i = 0; i < 4; i++) c->material.specular.v[i] = params[i];
        break;
    case GL_EMISSION:
        for (int i = 0; i < 4; i++) c->material.emission.v[i] = params[i];
        break;
    default:
        break;
    }
}

void glMaterialf(GLenum face, GLenum pname, GLfloat param) {
    (void)face;
    GLContext *c = glGetContext();
    if (pname == GL_SHININESS) c->material.shininess = param;
}

void gl_shade_vertex(GLContext *c, GLVertex *v) {
    if (!c->lighting_enabled) return;

    /* Transform normal by inverse-transpose of ModelView (rows of MV_inv) */
    if (c->matrix_model_view_inv_updated) {
        const M4 *mv = &c->matrix_stack[0][c->matrix_stack_ptr[0]];
        gl_M4_Inv(&c->matrix_model_view_inv, mv);
        c->matrix_model_view_inv_updated = 0;
    }
    const M4 *inv = &c->matrix_model_view_inv;
    V3 n;
    n.v[0] = v->normal.v[0]*inv->m[0][0] + v->normal.v[1]*inv->m[1][0] + v->normal.v[2]*inv->m[2][0];
    n.v[1] = v->normal.v[0]*inv->m[0][1] + v->normal.v[1]*inv->m[1][1] + v->normal.v[2]*inv->m[2][1];
    n.v[2] = v->normal.v[0]*inv->m[0][2] + v->normal.v[1]*inv->m[1][2] + v->normal.v[2]*inv->m[2][2];
    if (c->normalize_enabled) gl_V3_Norm(&n);

    float R = c->material.emission.v[0] + c->material.ambient.v[0] * c->ambient_light_model.v[0];
    float G = c->material.emission.v[1] + c->material.ambient.v[1] * c->ambient_light_model.v[1];
    float B = c->material.emission.v[2] + c->material.ambient.v[2] * c->ambient_light_model.v[2];

    for (int i = 0; i < TGL_MAX_LIGHTS; i++) {
        const GLLight *l = &c->lights[i];
        if (!l->enabled) continue;
        R += l->ambient.v[0] * c->material.ambient.v[0];
        G += l->ambient.v[1] * c->material.ambient.v[1];
        B += l->ambient.v[2] * c->material.ambient.v[2];

        V3 d;
        if (l->position.v[3] == 0.0f) {
            d.v[0] = l->position.v[0];
            d.v[1] = l->position.v[1];
            d.v[2] = l->position.v[2];
        } else {
            d.v[0] = l->position.v[0] - v->ec.v[0];
            d.v[1] = l->position.v[1] - v->ec.v[1];
            d.v[2] = l->position.v[2] - v->ec.v[2];
        }
        gl_V3_Norm(&d);
        float dot = n.v[0]*d.v[0] + n.v[1]*d.v[1] + n.v[2]*d.v[2];
        if (dot > 0.0f) {
            R += dot * l->diffuse.v[0] * c->material.diffuse.v[0];
            G += dot * l->diffuse.v[1] * c->material.diffuse.v[1];
            B += dot * l->diffuse.v[2] * c->material.diffuse.v[2];
        }
    }
    v->color.v[0] = clampf01(R);
    v->color.v[1] = clampf01(G);
    v->color.v[2] = clampf01(B);
    v->color.v[3] = c->material.diffuse.v[3];
}
