#include "zgl.h"

#define TGL_PI 3.14159265358979323846f
#define TGL_TWO_PI 6.28318530717958647692f
#define TGL_HALF_PI 1.57079632679489661923f

float tgl_sqrtf(float x) {
    if (x <= 0.0f) return 0.0f;
#if defined(__riscv_flen) && (__riscv_flen >= 32)
    float res;
    __asm__("fsqrt.s %0, %1" : "=f"(res) : "f"(x));
    return res;
#else
    union { float f; uint32_t i; } u = { x };
    u.i = 0x5f3759dfu - (u.i >> 1);
    float y = u.f;
    y = y * (1.5f - 0.5f * x * y * y);
    y = y * (1.5f - 0.5f * x * y * y);
    return x * y;
#endif
}

float tgl_sinf(float x) {
    /* Range-reduce to [-pi, pi] */
    int k = (int)(x * (1.0f / TGL_TWO_PI));
    x -= (float)k * TGL_TWO_PI;
    if (x > TGL_PI) x -= TGL_TWO_PI;
    if (x < -TGL_PI) x += TGL_TWO_PI;
    /* Fold to [-pi/2, pi/2] */
    if (x > TGL_HALF_PI) x = TGL_PI - x;
    else if (x < -TGL_HALF_PI) x = -TGL_PI - x;
    float x2 = x * x;
    return x * (1.0f + x2 * (-0.16666667f + x2 * (0.008333331f - x2 * 0.000198409f)));
}

float tgl_cosf(float x) {
    return tgl_sinf(x + TGL_HALF_PI);
}

void gl_M4_Id(M4 *a) {
    int i, j;
    for (i = 0; i < 4; i++)
        for (j = 0; j < 4; j++)
            a->m[i][j] = (i == j) ? 1.0f : 0.0f;
}

void gl_M4_Mul(M4 *c, const M4 *a, const M4 *b) {
    int i, j, k;
    M4 r;
    for (i = 0; i < 4; i++) {
        for (j = 0; j < 4; j++) {
            float sum = 0.0f;
            for (k = 0; k < 4; k++) {
                sum += a->m[i][k] * b->m[k][j];
            }
            r.m[i][j] = sum;
        }
    }
    *c = r;
}

void gl_M4_MulLeft(M4 *c, const M4 *b) {
    M4 a = *c;
    gl_M4_Mul(c, &a, b);
}

void gl_M4_InvOrtho(M4 *a, const M4 *b) {
    int i, j;
    for (i = 0; i < 3; i++) {
        for (j = 0; j < 3; j++) {
            a->m[i][j] = b->m[j][i];
        }
    }
    a->m[3][0] = 0.0f;
    a->m[3][1] = 0.0f;
    a->m[3][2] = 0.0f;
    a->m[3][3] = 1.0f;
    for (i = 0; i < 3; i++) {
        a->m[i][3] = -(b->m[0][i] * b->m[0][3] +
                       b->m[1][i] * b->m[1][3] +
                       b->m[2][i] * b->m[2][3]);
    }
}

void gl_M4_Inv(M4 *a, const M4 *b) {
    /* General 4x4 inverse via adjugate for normal matrix */
    M4 tmp;
    const float *m = &b->m[0][0];
    float *inv = &tmp.m[0][0];
    inv[0] = m[5]*m[10]*m[15] - m[5]*m[11]*m[14] - m[9]*m[6]*m[15] + m[9]*m[7]*m[14] + m[13]*m[6]*m[11] - m[13]*m[7]*m[10];
    inv[4] = -m[4]*m[10]*m[15] + m[4]*m[11]*m[14] + m[8]*m[6]*m[15] - m[8]*m[7]*m[14] - m[12]*m[6]*m[11] + m[12]*m[7]*m[10];
    inv[8] = m[4]*m[9]*m[15] - m[4]*m[11]*m[13] - m[8]*m[5]*m[15] + m[8]*m[7]*m[13] + m[12]*m[5]*m[11] - m[12]*m[7]*m[9];
    inv[12] = -m[4]*m[9]*m[14] + m[4]*m[10]*m[13] + m[8]*m[5]*m[14] - m[8]*m[6]*m[13] - m[12]*m[5]*m[10] + m[12]*m[6]*m[9];
    inv[1] = -m[1]*m[10]*m[15] + m[1]*m[11]*m[14] + m[9]*m[2]*m[15] - m[9]*m[3]*m[14] - m[13]*m[2]*m[11] + m[13]*m[3]*m[10];
    inv[5] = m[0]*m[10]*m[15] - m[0]*m[11]*m[14] - m[8]*m[2]*m[15] + m[8]*m[3]*m[14] + m[12]*m[2]*m[11] - m[12]*m[3]*m[10];
    inv[9] = -m[0]*m[9]*m[15] + m[0]*m[11]*m[13] + m[8]*m[1]*m[15] - m[8]*m[3]*m[13] - m[12]*m[1]*m[11] + m[12]*m[3]*m[9];
    inv[13] = m[0]*m[9]*m[14] - m[0]*m[10]*m[13] - m[8]*m[1]*m[14] + m[8]*m[2]*m[13] + m[12]*m[1]*m[10] - m[12]*m[2]*m[9];
    inv[2] = m[1]*m[6]*m[15] - m[1]*m[7]*m[14] - m[5]*m[2]*m[15] + m[5]*m[3]*m[14] + m[13]*m[2]*m[7] - m[13]*m[3]*m[6];
    inv[6] = -m[0]*m[6]*m[15] + m[0]*m[7]*m[14] + m[4]*m[2]*m[15] - m[4]*m[3]*m[14] - m[12]*m[2]*m[7] + m[12]*m[3]*m[6];
    inv[10] = m[0]*m[5]*m[15] - m[0]*m[7]*m[13] - m[4]*m[1]*m[15] + m[4]*m[3]*m[13] + m[12]*m[1]*m[7] - m[12]*m[3]*m[5];
    inv[14] = -m[0]*m[5]*m[14] + m[0]*m[6]*m[13] + m[4]*m[1]*m[14] - m[4]*m[2]*m[13] - m[12]*m[1]*m[6] + m[12]*m[2]*m[5];
    inv[3] = -m[1]*m[6]*m[11] + m[1]*m[7]*m[10] + m[5]*m[2]*m[11] - m[5]*m[3]*m[10] - m[9]*m[2]*m[7] + m[9]*m[3]*m[6];
    inv[7] = m[0]*m[6]*m[11] - m[0]*m[7]*m[10] - m[4]*m[2]*m[11] + m[4]*m[3]*m[10] + m[8]*m[2]*m[7] - m[8]*m[3]*m[6];
    inv[11] = -m[0]*m[5]*m[11] + m[0]*m[7]*m[9] + m[4]*m[1]*m[11] - m[4]*m[3]*m[9] - m[8]*m[1]*m[7] + m[8]*m[3]*m[5];
    inv[15] = m[0]*m[5]*m[10] - m[0]*m[6]*m[9] - m[4]*m[1]*m[10] + m[4]*m[2]*m[9] + m[8]*m[1]*m[6] - m[8]*m[2]*m[5];
    float det = m[0]*inv[0] + m[1]*inv[4] + m[2]*inv[8] + m[3]*inv[12];
    if (det == 0.0f) {
        gl_M4_Id(a);
        return;
    }
    float idet = 1.0f / det;
    for (int i = 0; i < 16; i++)
        (&a->m[0][0])[i] = inv[i] * idet;
}

void gl_M4_Rotate(M4 *a, float t, int u) {
    float s = tgl_sinf(t);
    float c = tgl_cosf(t);
    int v = (u + 1) % 3;
    int w = (u + 2) % 3;
    gl_M4_Id(a);
    a->m[v][v] = c;
    a->m[v][w] = -s;
    a->m[w][v] = s;
    a->m[w][w] = c;
}

void gl_M4_MulV4(V4 *a, const M4 *b, const V4 *c) {
    a->v[0] = b->m[0][0]*c->v[0] + b->m[0][1]*c->v[1] + b->m[0][2]*c->v[2] + b->m[0][3]*c->v[3];
    a->v[1] = b->m[1][0]*c->v[0] + b->m[1][1]*c->v[1] + b->m[1][2]*c->v[2] + b->m[1][3]*c->v[3];
    a->v[2] = b->m[2][0]*c->v[0] + b->m[2][1]*c->v[1] + b->m[2][2]*c->v[2] + b->m[2][3]*c->v[3];
    a->v[3] = b->m[3][0]*c->v[0] + b->m[3][1]*c->v[1] + b->m[3][2]*c->v[2] + b->m[3][3]*c->v[3];
}

void gl_M3_MulV3(V3 *a, const M4 *b, const V3 *c) {
    a->v[0] = b->m[0][0]*c->v[0] + b->m[0][1]*c->v[1] + b->m[0][2]*c->v[2];
    a->v[1] = b->m[1][0]*c->v[0] + b->m[1][1]*c->v[1] + b->m[1][2]*c->v[2];
    a->v[2] = b->m[2][0]*c->v[0] + b->m[2][1]*c->v[1] + b->m[2][2]*c->v[2];
}

int gl_V3_Norm(V3 *a) {
    float n = tgl_sqrtf(a->v[0]*a->v[0] + a->v[1]*a->v[1] + a->v[2]*a->v[2]);
    if (n == 0.0f) return 1;
    float inv = 1.0f / n;
    a->v[0] *= inv;
    a->v[1] *= inv;
    a->v[2] *= inv;
    return 0;
}
