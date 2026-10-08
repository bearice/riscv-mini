/* Memory, values and GW-BASIC number formatting.
 *
 * Numbers are printed from their exact decimal expansion: the significand is
 * converted with integer arithmetic, so single values are rounded from the
 * real stored float and not from a lossy float-to-decimal guess. */
#include "basic.h"
#include <string.h>

/* ------------------------------------------------------------------- heap */
#define HEAP_CLASSES 22                       /* 32 B .. 32 B << 21 = 64 MiB */
#define HEAP_HDR     16
static uint8_t  heap[BAS_HEAP_SIZE] __attribute__((aligned(16)));
static void    *heap_free[HEAP_CLASSES];
static uint32_t heap_top;

typedef struct { uint32_t cls; uint32_t magic; } HeapHeader;
#define HEAP_MAGIC 0x4b424153u                /* "SABK" */

void bas_heap_reset(void) {
    memset(heap_free, 0, sizeof(heap_free));
    heap_top = 0;
}

static int heap_class(uint32_t total) {
    int c = 0;
    uint32_t size = 32;
    while (size < total) { size <<= 1; ++c; }
    return c < HEAP_CLASSES ? c : -1;
}

void *bas_alloc(uint32_t bytes) {
    if (bytes > 0x3fffffffu) return 0;
    uint32_t total = (bytes + HEAP_HDR + 15u) & ~15u;   /* 16-byte block granularity */
    int cls = heap_class(total);
    if (cls < 0) return 0;
    uint32_t size = 32u << cls;
    void *block = heap_free[cls];
    if (block) heap_free[cls] = *(void **)block;
    else {
        if (heap_top + size > BAS_HEAP_SIZE) return 0;
        block = heap + heap_top;
        heap_top += size;
    }
    HeapHeader *head = (HeapHeader *)block;
    head->cls = (uint32_t)cls;
    head->magic = HEAP_MAGIC;
    return (uint8_t *)block + HEAP_HDR;
}

void bas_free(void *ptr) {
    if (!ptr) return;
    HeapHeader *head = (HeapHeader *)((uint8_t *)ptr - HEAP_HDR);
    if (head->magic != HEAP_MAGIC) bas_raise(E_INTERNAL);
    uint32_t cls = head->cls;
    head->magic = 0;
    *(void **)head = heap_free[cls];
    heap_free[cls] = head;
}

uint32_t bas_heap_used(void) { return heap_top; }

/* ----------------------------------------------------------------- strings */
BasStr *str_alloc(uint32_t len) {
    if (len > BAS_STR_MAX) bas_raise(E_STRING_TOO_LONG);
    BasStr *s = (BasStr *)bas_alloc(sizeof(BasStr) + len + 1);
    if (!s) bas_raise(E_OUT_OF_STRING_SPACE);
    s->refs = 1;
    s->len = len;
    s->data[len] = 0;
    return s;
}

BasStr *str_from(const char *data, uint32_t len) {
    BasStr *s = str_alloc(len);
    if (len) memcpy(s->data, data, len);
    return s;
}

BasStr *str_retain(BasStr *s) { if (s) s->refs++; return s; }

void str_release(BasStr *s) {
    if (!s) return;
    if (s->refs == 0) bas_raise(E_INTERNAL);
    if (--s->refs == 0) bas_free(s);
}

uint32_t str_len(const BasStr *s) { return s ? s->len : 0u; }
const char *str_ptr(const BasStr *s) { return s ? s->data : ""; }

BasStr *str_concat(const BasStr *a, const BasStr *b) {
    uint32_t la = str_len(a), lb = str_len(b);
    if (la + lb > BAS_STR_MAX) bas_raise(E_STRING_TOO_LONG);
    BasStr *s = str_alloc(la + lb);
    if (la) memcpy(s->data, str_ptr(a), la);
    if (lb) memcpy(s->data + la, str_ptr(b), lb);
    return s;
}

/* ------------------------------------------------------------------ values */
BasValue val_int(int32_t i) { BasValue v; v.type = T_INT; v.v.i = i; return v; }
BasValue val_sng(float f)   { BasValue v; v.type = T_SNG; v.v.f = f; return v; }
BasValue val_dbl(double d)  { BasValue v; v.type = T_DBL; v.v.d = d; return v; }
BasValue val_str(BasStr *s) { BasValue v; v.type = T_STR; v.v.s = s; return v; }
BasValue val_empty(void)    { return val_str(0); }

BasValue val_str_ref(const BasStr *s) { return val_str(str_retain((BasStr *)s)); }

void val_release(BasValue *v) {
    if (v->type == T_STR) str_release(v->v.s);
    v->type = T_STR;
    v->v.s = 0;
}

void val_set(BasValue *dst, const BasValue *src) {
    if (dst == src) return;
    if (src->type == T_STR) str_retain(src->v.s);
    val_release(dst);
    *dst = *src;
}

double val_number(const BasValue *v) {
    switch (v->type) {
    case T_INT: return (double)v->v.i;
    case T_SNG: return (double)v->v.f;
    case T_DBL: return v->v.d;
    default: bas_raise(E_TYPE_MISMATCH);
    }
    return 0.0;
}

int32_t val_integer(const BasValue *v) {
    double d = val_number(v);
    double r = d < 0 ? -((double)(long)(-d + 0.5)) : (double)(long)(d + 0.5);
    /* round half to even, the rule MS BASIC uses for CINT */
    double half = d < 0 ? -0.5 : 0.5;
    double f = d - (double)(long)d;
    if (f == half || f == -half) {
        double fl = (double)(long)d;
        double t = fl / 2.0;
        if (t == (double)(long)t) r = fl; else r = fl + half;
    }
    if (r < -32768.0 || r > 32767.0) bas_raise(E_OVERFLOW);
    return (int32_t)r;
}

int32_t val_int_of(const BasValue *v) {
    switch (v->type) {
    case T_INT: return v->v.i;
    case T_SNG: return (int32_t)v->v.f;
    case T_DBL: return (int32_t)v->v.d;
    default: bas_raise(E_TYPE_MISMATCH);
    }
    return 0;
}

float val_sng_of(const BasValue *v) {
    switch (v->type) {
    case T_INT: return (float)v->v.i;
    case T_SNG: return v->v.f;
    case T_DBL: return (float)v->v.d;
    default: bas_raise(E_TYPE_MISMATCH);
    }
    return 0;
}

double val_dbl_of(const BasValue *v) {
    switch (v->type) {
    case T_INT: return (double)v->v.i;
    case T_SNG: return (double)v->v.f;
    case T_DBL: return v->v.d;
    default: bas_raise(E_TYPE_MISMATCH);
    }
    return 0;
}

int val_truth(const BasValue *v) {
    if (v->type == T_STR) return str_len(v->v.s) != 0;
    return val_number(v) != 0.0;
}

int val_compare(const BasValue *a, const BasValue *b) {
    if (a->type == T_STR || b->type == T_STR) {
        if (a->type != T_STR || b->type != T_STR) bas_raise(E_TYPE_MISMATCH);
        uint32_t la = str_len(a->v.s), lb = str_len(b->v.s);
        uint32_t n = la < lb ? la : lb;
        int r = n ? memcmp(str_ptr(a->v.s), str_ptr(b->v.s), n) : 0;
        if (r) return r < 0 ? -1 : 1;
        if (la == lb) return 0;
        return la < lb ? -1 : 1;
    }
    double x = val_number(a), y = val_number(b);
    if (x < y) return -1;
    if (x > y) return 1;
    return 0;
}

void val_promote(BasValue *v, int type) {
    if (v->type == type) return;
    if (type == T_INT) { *v = val_int(val_integer(v)); return; }
    if (type == T_SNG) { *v = val_sng((float)val_number(v)); return; }
    if (type == T_DBL) { *v = val_dbl(val_number(v)); return; }
}

/* ----------------------------------------------------- exact decimal digits */
#define BIG_WORDS 82

typedef struct { uint32_t w[BIG_WORDS]; int n; } Big;

static void big_zero(Big *b) { b->n = 0; }
static int  big_is_zero(const Big *b) { return b->n == 0; }

static void big_mul_small(Big *b, uint32_t m) {
    if (b->n == 0) return;
    uint64_t carry = 0;
    for (int i = 0; i < b->n; ++i) {
        uint64_t t = (uint64_t)b->w[i] * m + carry;
        b->w[i] = (uint32_t)t;
        carry = t >> 32;
    }
    while (carry) {
        if (b->n >= BIG_WORDS) bas_raise(E_INTERNAL);
        b->w[b->n++] = (uint32_t)carry;
        carry >>= 32;
    }
}

static void big_shl(Big *b, int bits) {
    if (b->n == 0 || bits == 0) return;
    int words = bits >> 5, rest = bits & 31;
    if (rest) {
        uint32_t carry = 0;
        for (int i = 0; i < b->n; ++i) {
            uint64_t t = ((uint64_t)b->w[i] << rest) | carry;
            b->w[i] = (uint32_t)t;
            carry = (uint32_t)(t >> 32);
        }
        if (carry) {
            if (b->n >= BIG_WORDS) bas_raise(E_INTERNAL);
            b->w[b->n++] = carry;
        }
    }
    if (words) {
        if (b->n + words > BIG_WORDS) bas_raise(E_INTERNAL);
        for (int i = b->n - 1; i >= 0; --i) b->w[i + words] = b->w[i];
        for (int i = 0; i < words; ++i) b->w[i] = 0;
        b->n += words;
    }
}

static uint32_t big_div_small(Big *b, uint32_t d) {
    uint64_t rem = 0;
    for (int i = b->n - 1; i >= 0; --i) {
        uint64_t cur = (rem << 32) | b->w[i];
        b->w[i] = (uint32_t)(cur / d);
        rem = cur % d;
    }
    while (b->n > 0 && b->w[b->n - 1] == 0) --b->n;
    return (uint32_t)rem;
}

/* Exact decimal digits of |value|, most significant first. Returns the count
 * and the decimal exponent of the leading digit (value = d.ddd * 10^exp). */
static int exact_digits(double value, char *digits, int capacity, int *exp10) {
    uint64_t bits;
    memcpy(&bits, &value, sizeof(bits));
    int biased = (int)((bits >> 52) & 0x7ffu);
    uint64_t frac = bits & 0x000fffffffffffffull;
    uint64_t mant;
    int e2;
    if (biased == 0) { mant = frac; e2 = -1074; }
    else { mant = frac | (1ull << 52); e2 = biased - 1075; }
    Big b;
    big_zero(&b);
    b.w[0] = (uint32_t)mant;
    b.w[1] = (uint32_t)(mant >> 32);
    b.n = b.w[1] ? 2 : (b.w[0] ? 1 : 0);
    int shift = 0;                       /* value = b / 10^shift */
    if (e2 >= 0) big_shl(&b, e2);
    else {
        shift = -e2;
        for (int i = 0; i < shift; ++i) big_mul_small(&b, 5u);
    }
    char rev[900];
    int n = 0;
    while (!big_is_zero(&b)) {
        if (n >= (int)sizeof(rev)) bas_raise(E_INTERNAL);
        rev[n++] = (char)('0' + big_div_small(&b, 10u));
    }
    if (n == 0) { digits[0] = '0'; *exp10 = 0; return 1; }
    if (n > capacity) bas_raise(E_INTERNAL);
    for (int i = 0; i < n; ++i) digits[i] = rev[n - 1 - i];
    *exp10 = n - shift - 1;
    return n;
}

/* ----------------------------------------------- GW-BASIC number rendering */
static int emit_significant(char *out, int size, int negative, const char *digits,
                            int nd, int exp, int sig) {
    int len = 0;
    if (negative) out[len++] = '-';
    if (exp >= sig || exp < -4) {
        out[len++] = digits[0];
        if (nd > 1) {
            out[len++] = '.';
            for (int i = 1; i < nd; ++i) out[len++] = digits[i];
        }
        out[len++] = 'E';
        int e = exp;
        out[len++] = e < 0 ? '-' : '+';
        if (e < 0) e = -e;
        if (e >= 100) {
            out[len++] = (char)('0' + e / 100); e %= 100;
            out[len++] = (char)('0' + e / 10);
            out[len++] = (char)('0' + e % 10);
        } else {
            out[len++] = (char)('0' + e / 10);
            out[len++] = (char)('0' + e % 10);
        }
    } else if (exp >= 0) {
        for (int i = 0; i <= exp; ++i) out[len++] = i < nd ? digits[i] : '0';
        if (nd > exp + 1) {
            out[len++] = '.';
            for (int i = exp + 1; i < nd; ++i) out[len++] = digits[i];
        }
    } else {
        out[len++] = '.';
        for (int i = 0; i < -exp - 1; ++i) out[len++] = '0';
        for (int i = 0; i < nd; ++i) out[len++] = digits[i];
    }
    if (len >= size) bas_raise(E_INTERNAL);
    out[len] = 0;
    return len;
}

int bas_format_double(char *buf, int size, double value, int type) {
    if (value != value) { memcpy(buf, " 1.#QNAN", 9); return 8; }   /* never produced by BASIC */
    if (value == 0.0) { buf[0] = '0'; buf[1] = 0; return 1; }
    int negative = value < 0.0;
    if (negative) value = -value;
    int sig = type == T_DBL ? 16 : 7;
    char digits[900];
    int exp = 0;
    int nd = exact_digits(value, digits, (int)sizeof(digits), &exp);
    if (nd > sig) {
        int round_up = 0;
        if (digits[sig] > '5') round_up = 1;
        else if (digits[sig] == '5') round_up = 1;      /* half away from zero */
        nd = sig;
        if (round_up) {
            int i = nd - 1;
            while (i >= 0 && digits[i] == '9') { digits[i] = '0'; --i; }
            if (i < 0) {
                for (int j = nd - 1; j > 0; --j) digits[j] = digits[j - 1];
                digits[0] = '1';
                ++exp;
            } else digits[i]++;
        }
    }
    while (nd > 1 && digits[nd - 1] == '0') --nd;       /* strip trailing zeros */
    return emit_significant(buf, size, negative, digits, nd, exp, sig);
}

int bas_format_number(char *buf, int size, const BasValue *value) {
    if (value->type == T_INT) {
        int len = 0;
        int32_t i = value->v.i;
        if (i < 0) { buf[len++] = '-'; i = -i; }
        char tmp[12];
        int n = 0;
        if (i == 0) tmp[n++] = '0';
        while (i > 0) { tmp[n++] = (char)('0' + i % 10); i /= 10; }
        while (n > 0) buf[len++] = tmp[--n];
        buf[len] = 0;
        return len;
    }
    if (value->type == T_SNG) return bas_format_double(buf, size, (double)value->v.f, T_SNG);
    return bas_format_double(buf, size, value->v.d, T_DBL);
}

/* --------------------------------------------------------------- arithmetic */
BasValue bas_arith(int op, const BasValue *a, const BasValue *b) {
    int ta = a->type, tb = b->type;
    /* '+' is the only operator that works on strings: it concatenates them. */
    if (op == OP_ADD && ta == T_STR && tb == T_STR)
        return val_str(str_concat(a->v.s, b->v.s));
    if (ta == T_STR || tb == T_STR) bas_raise(E_TYPE_MISMATCH);
    int div = ta == T_DBL || tb == T_DBL ? T_DBL : (ta == T_SNG || tb == T_SNG ? T_SNG : T_INT);
    switch (op) {
    case OP_ADD: case OP_SUB: case OP_MUL:
        if (div == T_INT) {
            int32_t r = op == OP_ADD ? a->v.i + b->v.i
                      : op == OP_SUB ? a->v.i - b->v.i : a->v.i * b->v.i;
            if (r < -32768 || r > 32767) bas_raise(E_OVERFLOW);
            return val_int(r);
        }
        if (div == T_SNG) {
            float x = ta == T_INT ? (float)a->v.i : a->v.f;
            float y = tb == T_INT ? (float)b->v.i : b->v.f;
            float r = op == OP_ADD ? x + y : op == OP_SUB ? x - y : x * y;
            return val_sng(r);
        } else {
            double x = val_number(a), y = val_number(b);
            double r = op == OP_ADD ? x + y : op == OP_SUB ? x - y : x * y;
            return val_dbl(r);
        }
    case OP_DIV: {
        double y = val_number(b);
        if (y == 0.0) bas_raise(E_DIV_ZERO);
        double x = val_number(a);
        double r = x / y;
        if (ta == T_INT && tb == T_INT) return val_sng((float)r);
        return div == T_DBL ? val_dbl(r) : val_sng((float)r);
    }
    case OP_IDIV: {
        int32_t x = val_integer(a), y = val_integer(b);
        if (y == 0) bas_raise(E_DIV_ZERO);
        return val_int(x / y);
    }
    case OP_MOD: {
        int32_t x = val_integer(a), y = val_integer(b);
        if (y == 0) bas_raise(E_DIV_ZERO);
        return val_int(x % y);
    }
    case OP_POW: {
        double x = val_number(a), y = val_number(b);
        int type = (ta == T_DBL || tb == T_DBL) ? T_DBL : T_SNG;
        if (x == 0.0) {
            if (y == 0.0) return type == T_DBL ? val_dbl(1.0) : val_sng(1.0f);
            if (y < 0.0) bas_raise(E_DIV_ZERO);
            return type == T_DBL ? val_dbl(0.0) : val_sng(0.0f);
        }
        double r = bas_pow(x, y);
        if (r != r) bas_raise(E_ILLEGAL_FUNCTION_CALL);
        return type == T_DBL ? val_dbl(r) : val_sng((float)r);
    }
    case OP_AND: case OP_OR: case OP_XOR: case OP_EQV: case OP_IMP: {
        int32_t x = val_integer(a), y = val_integer(b);
        int32_t r = op == OP_AND ? (x & y) : op == OP_OR ? (x | y) : op == OP_XOR ? (x ^ y)
                  : op == OP_EQV ? ~(x ^ y) : (~x | y);
        return val_int(r);
    }
    case OP_NEG: case OP_NOT: {
        if (op == OP_NEG) {
            if (ta == T_INT) {
                if (a->v.i == -32768) bas_raise(E_OVERFLOW);
                return val_int(-a->v.i);
            }
            if (ta == T_SNG) return val_sng(-a->v.f);
            return val_dbl(-a->v.d);
        }
        return val_int(~val_integer(a));
    }
    default: bas_raise(E_SYNTAX);
    }
    return val_int(0);
}

BasValue val_negate(const BasValue *a) { return bas_arith(OP_NEG, a, a); }

/* NOT is the 16-bit one's complement GW-BASIC defines for its integer type. */
BasValue val_bitnot(const BasValue *a)
{
    int32_t x = val_integer(a);

    if (x < -32768 || x > 32767)
        bas_raise(E_OVERFLOW);
    return val_int(~x & 0xFFFF);
}

/* Round to the nearest integer with halves going to the even neighbour.  Used by
 * the graphics statements, where GW-BASIC rounds a coordinate. */
int val_round_even(double d)
{
    int negative = d < 0.0;
    double a = negative ? -d : d;
    double fl = (double)(long long)a;
    double frac = a - fl;
    double r;

    if (frac > 0.5)
        r = fl + 1.0;
    else if (frac < 0.5)
        r = fl;
    else
        r = (fl == 2.0 * (double)(long long)(fl / 2.0)) ? fl : fl + 1.0;
    return (int)(negative ? -r : r);
}

uint32_t bas_heap_free(void)
{
    return (uint32_t)BAS_HEAP_SIZE - heap_top;
}

/* ------------------------------------------------------------- PRINT USING */

/* Render one value into a USING image.  `digits` counts the '#' positions before
 * the decimal point and `dec` those after it; the field is exactly
 * digits + (dec ? dec + 1 : 0) characters wide.  flags: 1 forces a sign,
 * 2 fills with '*' and 4 puts '$' next to the number.
 * Returns the length written, or size + 1 when the value will not fit. */
int bas_format_using(char *buf, int size, const BasValue *v, int digits, int dec,
                     int flags)
{
    char      ip[24];
    char      fp[24];
    int       ilen = 0, flen = 0;
    int       negative, signlen, pad, i, out = 0;
    int       stars = (flags & 2) != 0;
    int       dollars = (flags & 4) != 0;
    long long scaled, scale10 = 1, ipart, fpart;
    double    x;

    if (v->type == T_STR)
        bas_raise(E_TYPE_MISMATCH);
    x = val_number(v);
    negative = x < 0.0;
    if (negative)
        x = -x;
    if (dec < 0)
        dec = 0;
    if (dec > 9)
        dec = 9;                     /* a long long keeps the scaling exact */
    if (digits < 1)
        digits = 1;
    if (digits > 18)
        digits = 18;
    for (i = 0; i < dec; i++)
        scale10 *= 10;
    scaled = (long long)(x * (double)scale10 + 0.5);   /* round half away */
    ipart = scaled / scale10;
    fpart = scaled % scale10;

    /* integer part, most significant first */
    {
        char tmp[24];
        int  n = 0;

        if (ipart == 0)
            tmp[n++] = '0';
        while (ipart > 0) {
            tmp[n++] = (char)('0' + (int)(ipart % 10));
            ipart /= 10;
        }
        for (i = 0; i < n; i++)
            ip[i] = tmp[n - 1 - i];
        ilen = n;
    }
    /* fraction part, exactly `dec` digits */
    for (i = dec - 1; i >= 0; i--) {
        fp[i] = (char)('0' + (int)(fpart % 10));
        fpart /= 10;
    }
    flen = dec;

    signlen = negative ? 1 : ((flags & 1) ? 1 : 0);
    pad = digits - signlen - (dollars ? 1 : 0) - ilen;
    if (pad < 0)
        return size + 1;             /* the value does not fit the image */
    if (out + pad + signlen + (dollars ? 1 : 0) + ilen + flen + 1 > size)
        return size + 1;
    for (i = 0; i < pad; i++)
        buf[out++] = stars ? '*' : ' ';
    if (dollars)
        buf[out++] = '$';
    if (negative)
        buf[out++] = '-';
    else if (flags & 1)
        buf[out++] = '+';
    for (i = 0; i < ilen; i++)
        buf[out++] = ip[i];
    if (flen > 0) {
        buf[out++] = '.';
        for (i = 0; i < flen; i++)
            buf[out++] = fp[i];
    }
    buf[out] = '\0';
    return out;
}
