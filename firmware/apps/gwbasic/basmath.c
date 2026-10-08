/* Elementary functions without libm: the payload links -nostdlib, so EXP, LOG,
 * SIN, COS, TAN, ATN, SQR and ^ are computed here from range reduction plus
 * polynomial approximations. tests/gwbasic_test.c compares every function
 * against the host libm. */
#include "basic.h"

static union { double d; uint64_t u; } bits;

static double from_bits(uint64_t u) { bits.u = u; return bits.d; }
static uint64_t to_bits(double d) { bits.d = d; return bits.u; }

#define PI      3.14159265358979323846
#define PIO2_HI 1.57079632673412561417e+00
#define PIO2_MID 6.07710050650619224932e-11
#define PIO2_LO 2.02226624879595063154e-21
#define LN2_HI  6.93147180369123816490e-01
#define LN2_LO  1.90821492927058770002e-10
#define LOG2E   1.4426950408889634074
#define TWO_OPI 0.63661977236758134308

static double dfloor(double x) {
    if (x >= 9007199254740992.0 || x <= -9007199254740992.0) return x;
    double t = (double)(int64_t)x;
    if (t > x) t -= 1.0;
    return t;
}

static double dabs(double x) { return x < 0 ? -x : x; }

static double quiet_nan(void) { return from_bits(0x7ff8000000000000ull); }
static double infinity(void) { return from_bits(0x7ff0000000000000ull); }

/* x * 2^n for |n| <= 1024 without a loop over every exponent */
static double scale2(double x, int n) {
    if (x == 0.0 || n == 0) return x;
    while (n > 1000) { x *= from_bits(0x7fe0000000000000ull); n -= 1023; }
    while (n < -1000) { x *= from_bits(0x0010000000000000ull); n += 1022; }
    uint64_t u = to_bits(x);
    int e = (int)((u >> 52) & 0x7ffu) + n;
    if (e >= 0x7ff) return infinity();
    if (e <= 0) {                              /* subnormal result */
        double r = x;
        int steps = -n;
        while (steps > 0) { int s = steps > 64 ? 64 : steps; r *= from_bits(((uint64_t)(1023 - s)) << 52); steps -= s; }
        return r;
    }
    u = (u & ~(0x7ffull << 52)) | ((uint64_t)e << 52);
    return from_bits(u);
}

double bas_exp(double x) {
    if (x != x) return x;
    if (x > 709.782712893384) return infinity();
    if (x < -745.1332191019411) return 0.0;
    double k = dfloor(x * LOG2E + 0.5);
    double r = (x - k * LN2_HI) - k * LN2_LO;
    double p = 1.0 / 87178291200.0;
    p = p * r + 1.0 / 6227020800.0;
    p = p * r + 1.0 / 479001600.0;
    p = p * r + 1.0 / 39916800.0;
    p = p * r + 1.0 / 3628800.0;
    p = p * r + 1.0 / 362880.0;
    p = p * r + 1.0 / 40320.0;
    p = p * r + 1.0 / 5040.0;
    p = p * r + 1.0 / 720.0;
    p = p * r + 1.0 / 120.0;
    p = p * r + 1.0 / 24.0;
    p = p * r + 1.0 / 6.0;
    p = p * r + 0.5;
    p = p * r + 1.0;
    p = p * r + 1.0;
    return scale2(p, (int)k);
}

double bas_log(double x) {
    if (x != x || x < 0.0) return quiet_nan();
    if (x == 0.0) return -infinity();
    int e = 0;
    if (x < 2.2250738585072014e-308) {         /* subnormal: lift it first */
        x = scale2(x, 64);
        e -= 64;
    }
    uint64_t u = to_bits(x);
    e += (int)((u >> 52) & 0x7ffu) - 1023;
    u = (u & 0x000fffffffffffffull) | (1023ull << 52);
    double m = from_bits(u);                   /* m in [1,2) */
    if (m > 1.4142135623730951) { m *= 0.5; e += 1; }
    double s = (m - 1.0) / (m + 1.0);
    double t = s * s;
    double sum = 1.0 / 27.0;
    sum = sum * t + 1.0 / 25.0;
    sum = sum * t + 1.0 / 23.0;
    sum = sum * t + 1.0 / 21.0;
    sum = sum * t + 1.0 / 19.0;
    sum = sum * t + 1.0 / 17.0;
    sum = sum * t + 1.0 / 15.0;
    sum = sum * t + 1.0 / 13.0;
    sum = sum * t + 1.0 / 11.0;
    sum = sum * t + 1.0 / 9.0;
    sum = sum * t + 1.0 / 7.0;
    sum = sum * t + 1.0 / 5.0;
    sum = sum * t + 1.0 / 3.0;
    sum = sum * t + 1.0;
    return 2.0 * s * sum + (double)e * LN2_HI + (double)e * LN2_LO;
}

double bas_sqr(double x) {
    if (x != x) return x;
    if (x < 0.0) return quiet_nan();
    if (x == 0.0) return 0.0;
    uint64_t u = to_bits(x);
    double y = from_bits(0x5fe6eb50c7b537a9ull - (u >> 1));   /* 1/sqrt guess */
    y = y * (1.5 - 0.5 * x * y * y);
    y = y * (1.5 - 0.5 * x * y * y);
    y = y * (1.5 - 0.5 * x * y * y);
    y = y * (1.5 - 0.5 * x * y * y);
    y = y * (1.5 - 0.5 * x * y * y);
    y = y * (1.5 - 0.5 * x * y * y);
    double r = x * y;
    if (r <= 0.0) r = 0.0;
    return r;
}

static double sin_core(double r) {
    double t = r * r;
    double p = -1.0 / 1307674368000.0;
    p = p * t + 1.0 / 6227020800.0;
    p = p * t - 1.0 / 39916800.0;
    p = p * t + 1.0 / 362880.0;
    p = p * t - 1.0 / 5040.0;
    p = p * t + 1.0 / 120.0;
    p = p * t - 1.0 / 6.0;
    return r + r * t * p;
}

static double cos_core(double r) {
    double t = r * r;
    double p = 1.0 / 20922789888000.0;
    p = p * t - 1.0 / 87178291200.0;
    p = p * t + 1.0 / 479001600.0;
    p = p * t - 1.0 / 3628800.0;
    p = p * t + 1.0 / 40320.0;
    p = p * t - 1.0 / 720.0;
    p = p * t + 1.0 / 24.0;
    p = p * t - 0.5;
    return 1.0 + t * p;
}

/* reduce x to r in [-pi/4, pi/4]; returns the quadrant 0..3 */
static int reduce_quadrant(double x, double *r) {
    double k = dfloor(x * TWO_OPI + 0.5);
    *r = ((x - k * PIO2_HI) - k * PIO2_MID) - k * PIO2_LO;
    double q = k - 4.0 * dfloor(k * 0.25);
    return (int)q;
}

double bas_sin(double x) {
    if (x != x) return x;
    double r;
    int q = reduce_quadrant(x, &r);
    switch (q) {
    case 0: return sin_core(r);
    case 1: return cos_core(r);
    case 2: return -sin_core(r);
    default: return -cos_core(r);
    }
}

double bas_cos(double x) {
    if (x != x) return x;
    double r;
    int q = reduce_quadrant(x, &r);
    switch (q) {
    case 0: return cos_core(r);
    case 1: return -sin_core(r);
    case 2: return -cos_core(r);
    default: return sin_core(r);
    }
}

double bas_tan(double x) {
    double s = bas_sin(x), c = bas_cos(x);
    if (c == 0.0) return s >= 0 ? infinity() : -infinity();
    return s / c;
}

double bas_atn(double x) {
    if (x != x) return x;
    int negate = x < 0;
    if (negate) x = -x;
    int invert = x > 1.0;
    if (invert) x = 1.0 / x;
    /* two half-angle steps bring |x| below 0.2 */
    x = x / (1.0 + bas_sqr(1.0 + x * x));
    x = x / (1.0 + bas_sqr(1.0 + x * x));
    double t = x * x;
    double p = 1.0 / 29.0;
    p = p * t - 1.0 / 27.0;
    p = p * t + 1.0 / 25.0;
    p = p * t - 1.0 / 23.0;
    p = p * t + 1.0 / 21.0;
    p = p * t - 1.0 / 19.0;
    p = p * t + 1.0 / 17.0;
    p = p * t - 1.0 / 15.0;
    p = p * t + 1.0 / 13.0;
    p = p * t - 1.0 / 11.0;
    p = p * t + 1.0 / 9.0;
    p = p * t - 1.0 / 7.0;
    p = p * t + 1.0 / 5.0;
    p = p * t - 1.0 / 3.0;
    double r = 4.0 * (x + x * t * p);
    if (invert) r = PI / 2.0 - r;
    return negate ? -r : r;
}

double bas_pow(double x, double y) {
    if (y == dfloor(y) && dabs(y) <= 1.0e9) {
        int64_t n = (int64_t)(y < 0 ? -y : y);
        int negative_power = y < 0;
        int odd = (n & 1) != 0;
        double base = dabs(x);
        double r = 1.0, b = base;
        while (n > 0) {
            if (n & 1) r *= b;
            b *= b;
            n >>= 1;
        }
        if (x < 0 && odd) r = -r;
        if (negative_power) {
            if (r == 0.0) return infinity();
            r = 1.0 / r;
        }
        return r;
    }
    if (x < 0.0) return quiet_nan();
    if (x == 0.0) return y > 0 ? 0.0 : infinity();
    return bas_exp(y * bas_log(x));
}
