/* funcs.c - GW-BASIC built-in functions. */
#include "basic.h"
#include <string.h>

uint32_t bas_rnd_seed = 0;
static int   rnd_used;
static int   rnd_mode;              /* RND(-n) style seeding, 0 = none */
static double rnd_last;             /* RND(0) repeats the previous value */
static int   fn_errno;              /* ERR */
static int   fn_erl;                /* ERL */
static int   fn_pos;                /* POS(0): column of the next PRINT */
static int   fn_csrlin;             /* CSRLIN: row of the next PRINT */
static int   fn_wide = 80;          /* WIDTH */
static int   fn_lpos;               /* LPOS(0) */

/* ------------------------------------------------------------------ random */

static uint32_t xorshift32(uint32_t x)
{
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    return x;
}

double bas_rnd(void)
{
    if (!rnd_used) {
        rnd_used = 1;
        if (bas_rnd_seed == 0)
            bas_rnd_seed = bas_plat_ms() ^ 0x9e3779b9u;
    }
    if (rnd_mode) {
        bas_rnd_seed = (uint32_t)rnd_mode;
        rnd_mode = 0;
    }
    bas_rnd_seed = xorshift32(bas_rnd_seed ? bas_rnd_seed : 1u);
    /* 24 bits of mantissa: exactly representable in a single. */
    rnd_last = (double)(bas_rnd_seed & 0xFFFFFFu) / 16777216.0;
    return rnd_last;
}

void bas_randomize(int seed, int have_seed)
{
    if (have_seed) {
        bas_rnd_seed = (uint32_t)seed;
        rnd_used = 1;
    } else {
        bas_rnd_seed = 0;
        rnd_used = 0;
    }
}

/* ------------------------------------------------------------------ errors */

void bas_set_error_state(int code, int line)
{
    fn_errno = code;
    fn_erl = line;
}

int bas_get_error(void)
{
    return fn_errno;
}

int bas_get_erl(void)
{
    return fn_erl;
}

/* ------------------------------------------------------- print position hooks */

void bas_note_output(const char *s, unsigned n)
{
    unsigned i;

    for (i = 0; i < n; i++) {
        if (s[i] == '\n') {
            fn_pos = 0;
            fn_csrlin++;
            fn_lpos = 0;
        } else if (s[i] == '\r') {
            fn_pos = 0;
        } else if (s[i] == '\t') {
            fn_pos = (fn_pos + 8) & ~7;
        } else {
            fn_pos++;
            fn_lpos++;
        }
    }
    if (fn_pos >= fn_wide) {
        fn_pos = 0;
        fn_csrlin++;
    }
}

int bas_pos_value(void)
{
    return fn_pos;
}

void bas_set_width(int wide)
{
    fn_wide = wide;
}

/* --------------------------------------------------------------- conversions */

static BasValue to_int(double d)
{
    return val_int(val_round_even(d));
}

static BasValue to_single(double d)
{
    if (d > 3.4028234663852886e38 || d < -3.4028234663852886e38)
        bas_raise(E_OVERFLOW);
    return val_sng((float)d);
}

/* ------------------------------------------------------------------ helpers */

static double num_arg(const BasValue *a, int n)
{
    if (n < 1)
        bas_raise(E_MISSING_OPERAND);
    if (a[0].type == T_STR)
        bas_raise(E_TYPE_MISMATCH);
    return val_number(&a[0]);
}

static double num_arg2(const BasValue *a, int n, int idx)
{
    if (n <= idx)
        bas_raise(E_MISSING_OPERAND);
    if (a[idx].type == T_STR)
        bas_raise(E_TYPE_MISMATCH);
    return val_number(&a[idx]);
}

static const char *str_arg(const BasValue *a, int n, uint32_t *len)
{
    if (n < 1)
        bas_raise(E_MISSING_OPERAND);
    if (a[0].type != T_STR)
        bas_raise(E_TYPE_MISMATCH);
    *len = str_len(a[0].v.s);
    return str_ptr(a[0].v.s);
}

/* Integer arguments must be in range, like GW-BASIC's CINT checks. */
static int int_arg(const BasValue *a, int n, int idx)
{
    double d = num_arg2(a, n, idx);

    if (d < -32768.0 || d > 32767.0)
        bas_raise(E_ILLEGAL_FUNCTION_CALL);
    return val_round_even(d);
}

/* ------------------------------------------------------------------ table */

#define B(name, type, lo, hi) { name, type, lo, hi }

static const BasBuiltin builtins[] = {
    /* numeric */
    B("ABS", T_SNG, 1, 1), B("SGN", T_INT, 1, 1),
    B("INT", T_SNG, 1, 1), B("FIX", T_SNG, 1, 1),
    B("CINT", T_INT, 1, 1), B("CSNG", T_SNG, 1, 1), B("CDBL", T_DBL, 1, 1),
    B("SQR", T_SNG, 1, 1), B("EXP", T_SNG, 1, 1), B("LOG", T_SNG, 1, 1),
    B("SIN", T_SNG, 1, 1), B("COS", T_SNG, 1, 1), B("TAN", T_SNG, 1, 1),
    B("ATN", T_SNG, 1, 1),
    B("RND", T_SNG, -1, 1), B("TIMER", T_SNG, -1, 0),
    B("FRE", T_INT, 1, 1), B("POS", T_INT, 1, 1), B("CSRLIN", T_INT, -1, 0),
    B("ERR", T_INT, -1, 0), B("ERL", T_INT, -1, 0),
    B("SCREEN", T_INT, 2, 3), B("POINT", T_INT, 1, 2),
    B("LEN", T_INT, 1, 1), B("ASC", T_INT, 1, 1), B("VAL", T_SNG, 1, 1),
    B("LOC", T_INT, 1, 1), B("LOF", T_INT, 1, 1), B("EOF", T_INT, 1, 1),
    B("PEEK", T_INT, 1, 1), B("INP", T_INT, 1, 1),
    /* string */
    B("CHR$", T_STR, 1, 1), B("STR$", T_STR, 1, 1),
    B("LEFT$", T_STR, 1, 2), B("RIGHT$", T_STR, 1, 2), B("MID$", T_STR, 1, 3),
    B("INSTR", T_INT, 2, 3), B("SPACE$", T_STR, 1, 1), B("STRING$", T_STR, 2, 2),
    B("HEX$", T_STR, 1, 1), B("OCT$", T_STR, 1, 1),
    B("UCASE$", T_STR, 1, 1), B("LCASE$", T_STR, 1, 1),
    B("DATE$", T_STR, -1, 0), B("TIME$", T_STR, -1, 0),
    B("INKEY$", T_STR, -1, 0), B("INPUT$", T_STR, -1, 1),
    B("ENVIRON$", T_STR, 1, 1), B("LOC$", T_STR, 1, 1),
    B("TAB(", T_INT, 1, 1), B("SPC(", T_INT, 1, 1),
};

const BasBuiltin *bas_builtin_find(const char *name)
{
    unsigned i;

    for (i = 0; i < sizeof(builtins) / sizeof(builtins[0]); i++)
        if (strcmp(builtins[i].name, name) == 0)
            return &builtins[i];
    return 0;
}

/* --------------------------------------------------------------- evaluation */

static BasValue do_mid(const BasValue *a, int nargs)
{
    uint32_t    len;
    const char *s = str_arg(a, nargs, &len);
    int         start = int_arg(a, nargs, 1);
    int         count = -1;

    if (nargs >= 3)
        count = int_arg(a, nargs, 2);
    if (start < 1)
        bas_raise(E_ILLEGAL_FUNCTION_CALL);
    if ((uint32_t)start > len)
        return val_empty();
    {
        uint32_t from = (uint32_t)(start - 1);
        uint32_t take = len - from;

        if (count >= 0 && (uint32_t)count < take)
            take = (uint32_t)count;
        return val_str(str_from(s + from, take));
    }
}

static BasValue do_instr(const BasValue *a, int nargs)
{
    uint32_t    l1, l2;
    const char *s1, *s2;
    int         from = 1;
    int         i;

    if (nargs == 3) {
        from = int_arg(a, nargs, 0);
        s1 = str_arg(&a[1], 1, &l1);
        s2 = str_arg(&a[2], 1, &l2);
    } else {
        s1 = str_arg(a, nargs, &l1);
        s2 = str_arg(&a[1], 1, &l2);
    }
    if (from < 1)
        bas_raise(E_ILLEGAL_FUNCTION_CALL);
    if ((uint32_t)from > l1 || l2 == 0)
        return val_int(0);
    for (i = from - 1; i + (int)l2 <= (int)l1; i++)
        if (memcmp(s1 + i, s2, l2) == 0)
            return val_int(i + 1);
    return val_int(0);
}

static BasValue do_val(const BasValue *a, int nargs)
{
    uint32_t    len;
    const char *s = str_arg(a, nargs, &len);
    char        buf[32];
    unsigned    i = 0;
    BasValue    v;
    const char *q;
    int         negative = 0;

    while (i < len && (s[i] == ' ' || s[i] == '\t'))
        i++;
    while (i < len && i < sizeof(buf) - 1) {
        char c = s[i];

        if (!((c >= '0' && c <= '9') || c == '.' || c == '+' || c == '-' ||
              c == 'E' || c == 'e' || c == 'D' || c == 'd'))
            break;
        buf[i] = c;
        i++;
    }
    buf[i] = 0;
    q = buf;
    if (*q == '+') {
        q++;
    } else if (*q == '-') {
        negative = 1;
        q++;
    }
    if (!bas_scan_number(&q, buf + i, &v))
        return val_sng(0.0f);
    if (negative)
        v = bas_arith(OP_NEG, &v, &v);
    return v;
}

static BasValue do_left(const BasValue *a, int nargs)
{
    uint32_t len;
    const char *s = str_arg(a, nargs, &len);
    int n = int_arg(a, nargs, 1);

    if (n < 0)
        bas_raise(E_ILLEGAL_FUNCTION_CALL);
    if ((uint32_t)n > len)
        n = (int)len;
    return val_str(str_from(s, (uint32_t)n));
}

static BasValue do_right(const BasValue *a, int nargs)
{
    uint32_t len;
    const char *s = str_arg(a, nargs, &len);
    int n = int_arg(a, nargs, 1);

    if (n < 0)
        bas_raise(E_ILLEGAL_FUNCTION_CALL);
    if ((uint32_t)n > len)
        n = (int)len;
    return val_str(str_from(s + (len - (uint32_t)n), (uint32_t)n));
}

static BasValue do_string(const BasValue *a, int nargs)
{
    int         n = int_arg(a, nargs, 0);
    BasStr     *s;
    const char *src;
    uint32_t    slen;

    if (n < 0)
        bas_raise(E_ILLEGAL_FUNCTION_CALL);
    if (a[1].type == T_STR) {
        src = str_arg(&a[1], 2, &slen);
        if (slen == 0)
            bas_raise(E_ILLEGAL_FUNCTION_CALL);
        s = str_alloc((uint32_t)n);
        {
            int i;

            for (i = 0; i < n; i++)
                s->data[i] = src[i % (int)slen];
        }
    } else {
        int c = int_arg(a, nargs, 1);

        if (c < 0 || c > 255)
            bas_raise(E_ILLEGAL_FUNCTION_CALL);
        s = str_alloc((uint32_t)n);
        memset(s->data, c, (unsigned)n);
    }
    return val_str(s);
}

static BasValue do_case(const BasValue *a, int nargs, int upper)
{
    uint32_t  len;
    const char *s = str_arg(a, nargs, &len);
    BasStr   *out = str_alloc(len);
    uint32_t  i;

    for (i = 0; i < len; i++) {
        char c = s[i];

        if (upper && c >= 'a' && c <= 'z')
            c = (char)(c - 32);
        else if (!upper && c >= 'A' && c <= 'Z')
            c = (char)(c + 32);
        out->data[i] = c;
    }
    return val_str(out);
}

static BasValue fmt_base(double d, int base)
{
    char        buf[24];
    int         i = 0;
    uint32_t    v;
    int         neg = 0;

    if (d < -32768.0 || d > 65535.0)
        bas_raise(E_ILLEGAL_FUNCTION_CALL);
    if (d < 0.0) {
        neg = 1;
        v = (uint32_t)(int32_t)d & 0xFFFFu;   /* GW-BASIC prints the 16-bit form */
    } else {
        v = (uint32_t)d;
    }
    if (v == 0)
        return val_str(str_from("0", 1));
    while (v) {
        int digit = (int)(v % (uint32_t)base);

        buf[i++] = (char)(digit < 10 ? '0' + digit : digit - 10 + 'A');
        v /= (uint32_t)base;
    }
    if (neg)
        buf[i++] = '-';
    {
        int lo = 0, hi = i - 1;

        while (lo < hi) {
            char t = buf[lo];

            buf[lo] = buf[hi];
            buf[hi] = t;
            lo++;
            hi--;
        }
    }
    return val_str(str_from(buf, (uint32_t)i));
}

BasValue bas_builtin_call(const BasBuiltin *b, const char *name,
                          const BasValue *args, int nargs)
{
    (void)name;
    if (b->min_args >= 0 && nargs < b->min_args)
        bas_raise(E_MISSING_OPERAND);
    if (nargs > b->max_args)
        bas_raise(E_ILLEGAL_FUNCTION_CALL);

    if (strcmp(b->name, "ABS") == 0) {
        double d = num_arg(args, nargs);

        if (args[0].type == T_DBL)
            return val_dbl(d < 0 ? -d : d);
        return to_single(d < 0 ? -d : d);
    }
    if (strcmp(b->name, "SGN") == 0) {
        double d = num_arg(args, nargs);

        return val_int(d > 0 ? 1 : (d < 0 ? -1 : 0));
    }
    if (strcmp(b->name, "INT") == 0) {
        double d = num_arg(args, nargs);
        double r = d;

        if (r >= 0) {
            while (r > 1e18)
                break;
            r = (double)(int64_t)r;
            if (r > d)
                r -= 1.0;
        } else {
            r = (double)(int64_t)r;
            if (r > d)
                r -= 1.0;
        }
        if (args[0].type == T_DBL)
            return val_dbl(r);
        return to_single(r);
    }
    if (strcmp(b->name, "FIX") == 0) {
        double d = num_arg(args, nargs);
        double r = (double)(int64_t)d;

        if (args[0].type == T_DBL)
            return val_dbl(r);
        return to_single(r);
    }
    if (strcmp(b->name, "CINT") == 0)
        return to_int(num_arg(args, nargs));
    if (strcmp(b->name, "CSNG") == 0)
        return to_single(num_arg(args, nargs));
    if (strcmp(b->name, "CDBL") == 0)
        return val_dbl(num_arg(args, nargs));
    if (strcmp(b->name, "SQR") == 0) {
        double d = num_arg(args, nargs);

        if (d < 0.0)
            bas_raise(E_ILLEGAL_FUNCTION_CALL);
        if (args[0].type == T_DBL)
            return val_dbl(bas_sqr(d));
        return to_single(bas_sqr(d));
    }
    if (strcmp(b->name, "EXP") == 0) {
        double d = num_arg(args, nargs);

        if (args[0].type == T_DBL)
            return val_dbl(bas_exp(d));
        return to_single(bas_exp(d));
    }
    if (strcmp(b->name, "LOG") == 0) {
        double d = num_arg(args, nargs);

        if (d <= 0.0)
            bas_raise(E_ILLEGAL_FUNCTION_CALL);
        if (args[0].type == T_DBL)
            return val_dbl(bas_log(d));
        return to_single(bas_log(d));
    }
    if (strcmp(b->name, "SIN") == 0) {
        double d = num_arg(args, nargs);

        if (args[0].type == T_DBL)
            return val_dbl(bas_sin(d));
        return to_single(bas_sin(d));
    }
    if (strcmp(b->name, "COS") == 0) {
        double d = num_arg(args, nargs);

        if (args[0].type == T_DBL)
            return val_dbl(bas_cos(d));
        return to_single(bas_cos(d));
    }
    if (strcmp(b->name, "TAN") == 0) {
        double d = num_arg(args, nargs);

        if (args[0].type == T_DBL)
            return val_dbl(bas_tan(d));
        return to_single(bas_tan(d));
    }
    if (strcmp(b->name, "ATN") == 0) {
        double d = num_arg(args, nargs);

        if (args[0].type == T_DBL)
            return val_dbl(bas_atn(d));
        return to_single(bas_atn(d));
    }
    if (strcmp(b->name, "RND") == 0) {
        int as_double = 0;

        if (nargs >= 1) {
            if (args[0].type == T_STR)
                bas_raise(E_TYPE_MISMATCH);
            if (args[0].type == T_DBL)
                as_double = 1;
            {
                double d = val_number(&args[0]);

                if (d < 0.0) {
                    rnd_mode = val_round_even(d);
                    if (rnd_mode < 0)
                        rnd_mode = -rnd_mode;
                } else if (d == 0.0) {
                    return as_double ? val_dbl(rnd_last) : val_sng((float)rnd_last);
                }
            }
        }
        if (as_double)
            return val_dbl(bas_rnd());
        return val_sng((float)bas_rnd());
    }
    if (strcmp(b->name, "TIMER") == 0)
        return val_sng((float)((double)bas_plat_ms() / 1000.0));
    if (strcmp(b->name, "FRE") == 0) {
        if (args[0].type == T_STR) {
            uint32_t len;

            (void)str_arg(args, nargs, &len);
            return val_int((int32_t)(BAS_STR_MAX - 64));
        }
        return val_int((int32_t)(bas_heap_free() / 4u));
    }
    if (strcmp(b->name, "POS") == 0) {
        num_arg(args, nargs);
        return val_int(fn_pos);
    }
    if (strcmp(b->name, "CSRLIN") == 0)
        return val_int(fn_csrlin);
    if (strcmp(b->name, "ERR") == 0)
        return val_int(fn_errno);
    if (strcmp(b->name, "ERL") == 0)
        return val_int(fn_erl);
    if (strcmp(b->name, "SCREEN") == 0) {
        int row = int_arg(args, nargs, 0);
        int col = int_arg(args, nargs, 1);
        int mode = (nargs >= 3) ? int_arg(args, nargs, 2) : 0;

        return val_int(bas_screen_function(row, col, mode));
    }
    if (strcmp(b->name, "POINT") == 0) {
        if (nargs == 1) {
            double x = num_arg(args, nargs);

            return val_int(bas_point(x, x));
        }
        return val_int(bas_point(num_arg(args, nargs), num_arg2(args, nargs, 1)));
    }
    if (strcmp(b->name, "LEN") == 0) {
        if (args[0].type == T_STR) {
            uint32_t len;

            (void)str_arg(args, nargs, &len);
            return val_int((int32_t)len);
        }
        /* LEN of a numeric variable: GW-BASIC reports the storage size. */
        return val_int(args[0].type == T_DBL ? 8 : (args[0].type == T_INT ? 2 : 4));
    }
    if (strcmp(b->name, "ASC") == 0) {
        uint32_t    len;
        const char *s = str_arg(args, nargs, &len);

        if (len == 0)
            bas_raise(E_ILLEGAL_FUNCTION_CALL);
        return val_int((unsigned char)s[0]);
    }
    if (strcmp(b->name, "VAL") == 0)
        return do_val(args, nargs);
    if (strcmp(b->name, "LOC") == 0 || strcmp(b->name, "LOF") == 0 ||
        strcmp(b->name, "EOF") == 0)
        bas_raise(E_BAD_FILE_MODE);
    if (strcmp(b->name, "PEEK") == 0 || strcmp(b->name, "INP") == 0)
        bas_raise(E_UNAVAILABLE);
    if (strcmp(b->name, "CHR$") == 0) {
        int c = int_arg(args, nargs, 0);

        if (c < 0 || c > 255)
            bas_raise(E_ILLEGAL_FUNCTION_CALL);
        {
            char one = (char)c;

            return val_str(str_from(&one, 1));
        }
    }
    if (strcmp(b->name, "STR$") == 0) {
        /* GW-BASIC reserves a leading space for the sign of a value that is not
         * negative, exactly as PRINT does. */
        char tmp[48];
        char buf[50];
        int  n, at = 0, i;

        if (args[0].type == T_STR)
            bas_raise(E_TYPE_MISMATCH);
        n = bas_format_number(tmp, sizeof(tmp), &args[0]);
        if (tmp[0] != '-')
            buf[at++] = ' ';
        for (i = 0; i < n; i++)
            buf[at++] = tmp[i];
        return val_str(str_from(buf, (uint32_t)at));
    }
    if (strcmp(b->name, "LEFT$") == 0)
        return do_left(args, nargs);
    if (strcmp(b->name, "RIGHT$") == 0)
        return do_right(args, nargs);
    if (strcmp(b->name, "MID$") == 0)
        return do_mid(args, nargs);
    if (strcmp(b->name, "INSTR") == 0)
        return do_instr(args, nargs);
    if (strcmp(b->name, "SPACE$") == 0) {
        int n = int_arg(args, nargs, 0);

        if (n < 0)
            bas_raise(E_ILLEGAL_FUNCTION_CALL);
        {
            BasStr *s = str_alloc((uint32_t)n);

            memset(s->data, ' ', (unsigned)n);
            return val_str(s);
        }
    }
    if (strcmp(b->name, "STRING$") == 0)
        return do_string(args, nargs);
    if (strcmp(b->name, "HEX$") == 0)
        return fmt_base(num_arg(args, nargs), 16);
    if (strcmp(b->name, "OCT$") == 0)
        return fmt_base(num_arg(args, nargs), 8);
    if (strcmp(b->name, "UCASE$") == 0)
        return do_case(args, nargs, 1);
    if (strcmp(b->name, "LCASE$") == 0)
        return do_case(args, nargs, 0);
    if (strcmp(b->name, "DATE$") == 0)
        return val_str(str_from("01-01-80", 8));
    if (strcmp(b->name, "TIME$") == 0) {
        char     buf[16];
        uint32_t ms = bas_plat_ms();
        int      h = (int)(ms / 3600000u) % 24;
        int      m = (int)(ms / 60000u) % 60;
        int      s = (int)(ms / 1000u) % 60;
        int      n;

        n = 0;
        buf[n++] = (char)('0' + h / 10);
        buf[n++] = (char)('0' + h % 10);
        buf[n++] = ':';
        buf[n++] = (char)('0' + m / 10);
        buf[n++] = (char)('0' + m % 10);
        buf[n++] = ':';
        buf[n++] = (char)('0' + s / 10);
        buf[n++] = (char)('0' + s % 10);
        return val_str(str_from(buf, (uint32_t)n));
    }
    if (strcmp(b->name, "INKEY$") == 0) {
        int c = con_getc();

        if (c < 0)
            return val_empty();
        {
            char one = (char)c;

            return val_str(str_from(&one, 1));
        }
    }
    if (strcmp(b->name, "INPUT$") == 0) {
        int n = (nargs >= 1) ? int_arg(args, nargs, 0) : -1;

        if (n < 0)
            bas_raise(E_ILLEGAL_FUNCTION_CALL);
        {
            char    *buf = (char *)bas_alloc((uint32_t)n + 1u);
            BasStr  *s;
            int      i;

            if (!buf)
                bas_raise(E_OUT_OF_STRING_SPACE);
            for (i = 0; i < n; i++) {
                int c = con_getc_wait();

                if (c < 0)
                    break;
                buf[i] = (char)c;
            }
            s = str_from(buf, (uint32_t)i);
            bas_free(buf);
            return val_str(s);
        }
    }
    if (strcmp(b->name, "ENVIRON$") == 0)
        return val_empty();
    if (strcmp(b->name, "LOC$") == 0)
        bas_raise(E_BAD_FILE_MODE);
    if (strcmp(b->name, "TAB(") == 0 || strcmp(b->name, "SPC(") == 0) {
        /* Only meaningful inside PRINT; the statement layer handles those. */
        bas_raise(E_ILLEGAL_FUNCTION_CALL);
    }
    bas_raise(E_ILLEGAL_FUNCTION_CALL);
    return val_int(0);
}

/* A bare name with no parentheses: INKEY$ and the zero-argument functions. */
BasValue bas_builtin_bare(const BasBuiltin *b)
{
    if (b->min_args > 0)
        bas_raise(E_SYNTAX);
    return bas_builtin_call(b, b->name, 0, 0);
}

/* Assignment-style builtins: `INKEY$ = A$` is a no-op in GW-BASIC, `LOC$ =`
 * and `ENVIRON$ =` are not supported here. */
int bas_builtin_take(const BasBuiltin *b, BasValue *slot)
{
    (void)slot;
    if (strcmp(b->name, "INKEY$") == 0)
        return 1;
    return 0;
}
