/* prog.c - program store, variable/array storage and lexical primitives for
 * the payload-side GW-BASIC interpreter.
 *
 * The interpreter keeps the text of every program line exactly as the user
 * typed it (upper-cased outside strings and comments, like GW-BASIC does) and
 * tokenises it on the fly.  That costs a little speed but keeps the memory
 * footprint small and makes LIST, EDIT and RENUM trivial. */
#include "basic.h"
#include <string.h>

BasLine *bas_program;
BasLine *bas_current;
int      bas_err;
int      bas_err_line;
uint8_t  bas_option_base;

static BasVar  vars[BAS_MAX_VARS];
static uint8_t def_type[26];        /* DEFINT/DEFSNG/DEFDBL/DEFSTR per letter */
static int     vars_ready;

/* ------------------------------------------------------------------- errors */

const char *bas_error_text(int code)
{
    switch (code) {
    case E_NEXT_WITHOUT_FOR:      return "NEXT without FOR";
    case E_SYNTAX:                return "Syntax error";
    case E_RETURN_WITHOUT_GOSUB:  return "RETURN without GOSUB";
    case E_OUT_OF_DATA:           return "Out of DATA";
    case E_ILLEGAL_FUNCTION_CALL: return "Illegal function call";
    case E_OVERFLOW:              return "Overflow";
    case E_OUT_OF_MEMORY:         return "Out of memory";
    case E_UNDEFINED_LINE:        return "Undefined line number";
    case E_SUBSCRIPT:             return "Subscript out of range";
    case E_DUPLICATE_DEF:         return "Duplicate definition";
    case E_DIV_ZERO:              return "Division by zero";
    case E_ILLEGAL_DIRECT:        return "Illegal direct";
    case E_TYPE_MISMATCH:         return "Type mismatch";
    case E_OUT_OF_STRING_SPACE:   return "Out of string space";
    case E_STRING_TOO_LONG:       return "String too long";
    case E_STRING_COMPLEX:        return "String expression too complex";
    case E_CANT_CONTINUE:         return "Can't continue";
    case E_UNDEFINED_FUNCTION:    return "Undefined user function";
    case E_NO_RESUME:             return "No RESUME";
    case E_RESUME_WITHOUT_ERROR:  return "RESUME without error";
    case E_MISSING_OPERAND:       return "Missing operand";
    case E_LINE_BUFFER_OVERFLOW:  return "Line buffer overflow";
    case E_DEVICE_TIMEOUT:        return "Device timeout";
    case E_DEVICE_FAULT:          return "Device fault";
    case E_FOR_WITHOUT_NEXT:      return "FOR without NEXT";
    case E_OUT_OF_PAPER:          return "Out of paper";
    case E_WHILE_WITHOUT_WEND:    return "WHILE without WEND";
    case E_WEND_WITHOUT_WHILE:    return "WEND without WHILE";
    case E_FIELD_OVERFLOW:        return "FIELD overflow";
    case E_INTERNAL:              return "Internal error";
    case E_BAD_FILE_NUMBER:       return "Bad file number";
    case E_FILE_NOT_FOUND:        return "File not found";
    case E_BAD_FILE_MODE:         return "Bad file mode";
    case E_FILE_ALREADY_OPEN:     return "File already open";
    case E_DEVICE_IO:             return "Device I/O error";
    case E_FILE_EXISTS:           return "File already exists";
    case E_DISK_FULL:             return "Disk full";
    case E_INPUT_PAST_END:        return "Input past end";
    case E_BAD_RECORD:            return "Bad record number";
    case E_BAD_FILE_NAME:         return "Bad file name";
    case E_DIRECT_IN_FILE:        return "Illegal direct in file";
    case E_TOO_MANY_FILES:        return "Too many files";
    case E_DEVICE_UNAVAILABLE:    return "Device unavailable";
    case E_COMM_BUFFER:           return "Communication buffer overflow";
    case E_PERMISSION:            return "Permission denied";
    case E_DISK_NOT_READY:        return "Disk not ready";
    case E_DISK_MEDIA:            return "Disk media error";
    case E_ADVANCED:              return "Advanced feature";
    case E_PATH_FILE:             return "Path/File access error";
    case E_PATH_NOT_FOUND:        return "Path not found";
    case E_BREAK:                 return "Break";
    case E_UNAVAILABLE:           return "Feature unavailable in this build";
    default:                      return "Unprintable error";
    }
}

void bas_raise(int code)
{
    bas_err = code;
    bas_err_line = bas_current ? (int)bas_current->number : 0;
    bas_set_error_state(code, bas_err_line);   /* ERR / ERL see the last error */
    if (bas_error_armed) {
        bas_error_armed = 0;
        bas_longjmp(&bas_error_jmp, 1);
    }
    bas_uncaught_error(code);
}

void bas_raise_line(int code, int line)
{
    bas_err = code;
    bas_err_line = line;
    bas_set_error_state(code, line);
    if (bas_error_armed) {
        bas_error_armed = 0;
        bas_longjmp(&bas_error_jmp, 1);
    }
    bas_uncaught_error(code);
}

/* ------------------------------------------------------------------ program */

static BasLine *line_alloc(int number, const char *text, int len)
{
    BasLine *l = bas_alloc((uint32_t)sizeof(BasLine) + (uint32_t)len + 1u);

    if (!l)
        bas_raise(E_OUT_OF_MEMORY);
    l->number = (uint16_t)number;
    l->len    = (uint16_t)len;
    l->next   = 0;
    if (len > 0)
        memcpy(l->text, text, (unsigned)len);
    l->text[len] = 0;
    return l;
}

BasLine *bas_line_find(int number)
{
    BasLine *l = bas_program;

    while (l && (int)l->number < number)
        l = l->next;
    if (l && (int)l->number == number)
        return l;
    return 0;
}

void bas_line_insert(int number, const char *text, int len)
{
    BasLine **pp = &bas_program;
    BasLine  *nl;

    if (number < 0 || number > 65529)
        bas_raise(E_SYNTAX);
    if (len < 0 || (uint32_t)len > BAS_LINE_MAX - 1)
        bas_raise(E_LINE_BUFFER_OVERFLOW);
    while (*pp && (int)(*pp)->number < number)
        pp = &(*pp)->next;
    nl = line_alloc(number, text, len);
    if (*pp && (int)(*pp)->number == number) {
        BasLine *old = *pp;

        nl->next = old->next;
        *pp = nl;
        if (bas_current == old)
            bas_current = nl;
        bas_free(old);
        return;
    }
    nl->next = *pp;
    *pp = nl;
}

int bas_line_delete(int number)
{
    BasLine **pp = &bas_program;

    while (*pp && (int)(*pp)->number < number)
        pp = &(*pp)->next;
    if (*pp && (int)(*pp)->number == number) {
        BasLine *old = *pp;

        *pp = old->next;
        if (bas_current == old)
            bas_current = 0;
        bas_free(old);
        return 1;
    }
    return 0;
}

void bas_program_clear(void)
{
    BasLine *l = bas_program;

    while (l) {
        BasLine *next = l->next;

        bas_free(l);
        l = next;
    }
    bas_program = 0;
    bas_current = 0;
}

int bas_program_lines(void)
{
    BasLine *l = bas_program;
    int      n = 0;

    while (l) {
        n++;
        l = l->next;
    }
    return n;
}

/* ---------------------------------------------------------------- variables */

static uint32_t var_hash(const char *key)
{
    return ((uint32_t)(uint8_t)key[0] * 7u + (uint32_t)(uint8_t)key[1] * 31u +
            (uint32_t)(uint8_t)key[2] * 131u) & (BAS_MAX_VARS - 1);
}

static int suffix_type(int c)
{
    switch (c) {
    case '$': return T_STR;
    case '%': return T_INT;
    case '!': return T_SNG;
    case '#': return T_DBL;
    default:  return 0;
    }
}

int bas_name_type(const char *name, int len)
{
    int t;

    if (len <= 0)
        return T_SNG;
    t = suffix_type((uint8_t)name[len - 1]);
    if (t)
        return t;
    if (!vars_ready) {
        int i;

        for (i = 0; i < 26; i++)
            def_type[i] = T_SNG;
        vars_ready = 1;
    }
    {
        int up = bas_upcase((uint8_t)name[0]);

        if (up >= 'A' && up <= 'Z')
            return def_type[up - 'A'];
    }
    return T_SNG;
}

static void var_init(BasVar *v, const char *name, int len, int type)
{
    int up0 = bas_upcase((uint8_t)name[0]);
    int up1 = (len > 1) ? bas_upcase((uint8_t)name[1]) : 0;

    if (up1 == '$' || up1 == '%' || up1 == '!' || up1 == '#')
        up1 = 0;                     /* suffix is not a significant character */
    v->key[0] = (char)up0;
    v->key[1] = (char)up1;
    v->key[2] = (char)type;
    v->key[3] = 0;
    v->type = (uint8_t)type;
    v->is_array = 0;
    v->used = 0;
    v->array = 0;
    v->value.type = (uint8_t)type;
    v->value.v.d = 0.0;
    if (type == T_STR)
        v->value.v.s = 0;
}

BasVar *bas_var(const char *name, int len, int create)
{
    int type, i, idx, found = -1;

    if (len <= 0)
        bas_raise(E_SYNTAX);
    type = bas_name_type(name, len);
    {
        char key[4];
        int  up0 = bas_upcase((uint8_t)name[0]);
        int  up1 = (len > 1) ? bas_upcase((uint8_t)name[1]) : 0;

        if (up1 == '$' || up1 == '%' || up1 == '!' || up1 == '#')
            up1 = 0;
        key[0] = (char)up0;
        key[1] = (char)up1;
        key[2] = (char)type;
        key[3] = 0;
        idx = (int)var_hash(key);
        for (i = 0; i < BAS_MAX_VARS; i++) {
            int slot = (idx + i) & (BAS_MAX_VARS - 1);
            BasVar *v = &vars[slot];

            if (v->key[0] == 0) {
                if (found < 0)
                    found = slot;    /* first empty slot on the probe path */
                break;
            }
            if (v->key[0] == key[0] && v->key[1] == key[1] && v->key[2] == key[2])
                return v;
        }
        if (!create)
            return 0;
        if (found < 0)
            bas_raise(E_OUT_OF_MEMORY);
        var_init(&vars[found], name, len, type);
        return &vars[found];
    }
}

BasValue bas_var_value(const char *name)
{
    BasVar *v = bas_var(name, (int)strlen(name), 1);

    v->used = 1;
    if (v->is_array)
        return val_int(0);
    if (v->type == T_STR)
        return val_str(str_retain(v->value.v.s));
    return v->value;
}

void bas_def_type(int first, int last, int type)
{
    int i;

    if (!vars_ready) {
        (void)bas_name_type("A", 1);
    }
    for (i = first; i <= last; i++)
        if (i >= 'A' && i <= 'Z')
            def_type[i - 'A'] = (uint8_t)type;
}

/* DEFINT A-Z, DEFSNG B,C ... : parse the letter list after the keyword. */
void bas_def_type_range(const char **p, const char *end, int type)
{
    const char *q = *p;

    for (;;) {
        int first, last;

        bas_skip_spaces(&q, end);
        if (q >= end || !bas_is_name_start(*q))
            bas_raise(E_SYNTAX);
        first = bas_upcase(*q);
        q++;
        last = first;
        bas_skip_spaces(&q, end);
        if (q < end && *q == '-') {
            q++;
            bas_skip_spaces(&q, end);
            if (q >= end || !bas_is_name_start(*q))
                bas_raise(E_SYNTAX);
            last = bas_upcase(*q);
            q++;
        }
        if (first < 'A' || last > 'Z' || first > last)
            bas_raise(E_SYNTAX);
        bas_def_type(first, last, type);
        bas_skip_spaces(&q, end);
        if (q < end && *q == ',') {
            q++;
            continue;
        }
        break;
    }
    *p = q;
}

/* DIM a(n), b(m TO k) ...  Bounds are lower TO upper, or a single upper bound
 * that starts at OPTION BASE.  An array already used as a scalar is a
 * duplicate definition. */
void bas_dim_statement(const char **p, const char *end)
{
    const char *q = *p;

    for (;;) {
        char     name[16];
        BasVar  *var;
        int32_t  counts[BAS_MAX_DIMS];
        int      ndims = 0;

        bas_skip_spaces(&q, end);
        if (!bas_read_name(&q, end, name))
            bas_raise(E_SYNTAX);
        var = bas_var(name, (int)strlen(name), 1);
        bas_skip_spaces(&q, end);
        if (q < end && *q == '(') {
            q++;
            for (;;) {
                BasValue lo;
                int32_t  lower, upper;

                bas_skip_spaces(&q, end);
                lo = bas_expr(&q, end);
                if (lo.type == T_STR) {
                    val_release(&lo);
                    bas_raise(E_TYPE_MISMATCH);
                }
                lower = val_integer(&lo);
                val_release(&lo);
                bas_skip_spaces(&q, end);
                if (q < end && bas_keyword(&q, end, "TO")) {
                    BasValue h;

                    bas_skip_spaces(&q, end);
                    h = bas_expr(&q, end);
                    if (h.type == T_STR) {
                        val_release(&h);
                        bas_raise(E_TYPE_MISMATCH);
                    }
                    upper = val_integer(&h);
                    val_release(&h);
                } else {
                    upper = lower;
                    lower = bas_option_base;
                }
                if (upper < lower)
                    bas_raise(E_SUBSCRIPT);
                if (ndims >= BAS_MAX_DIMS)
                    bas_raise(E_SUBSCRIPT);
                counts[ndims++] = upper - lower + 1;
                bas_skip_spaces(&q, end);
                if (q < end && *q == ',') {
                    q++;
                    continue;
                }
                break;
            }
            if (q >= end || *q != ')')
                bas_raise(E_SYNTAX);
            q++;
        } else {
            counts[0] = 11;         /* DIM A without bounds: the GW default */
            ndims = 1;
        }
        if (var->is_array || var->used)
            bas_raise(E_DUPLICATE_DEF);
        (void)bas_array_dim(var, ndims, counts);
        bas_skip_spaces(&q, end);
        if (q < end && *q == ',') {
            q++;
            continue;
        }
        break;
    }
    *p = q;
}

void bas_array_clear(BasArray *a)
{
    int32_t i;

    if (!a)
        return;
    if (a->type == T_STR) {
        for (i = 0; i < a->total; i++)
            str_release(a->data[i].v.s);
    }
    bas_free(a);
}

void bas_vars_clear(void)
{
    int i;

    for (i = 0; i < BAS_MAX_VARS; i++) {
        BasVar *v = &vars[i];

        if (v->key[0] == 0)
            continue;
        if (v->is_array)
            bas_array_clear(v->array);
        else if (v->type == T_STR)
            str_release(v->value.v.s);
        v->key[0] = 0;
    }
}

BasArray *bas_array_dim(BasVar *var, int ndims, const int32_t *counts)
{
    BasArray *a;
    int32_t   total = 1;
    int       i;

    /* Only the DIM statement rejects a variable that was already used as a
     * scalar; an automatic dimension of a fresh variable is allowed. */
    if (var->is_array)
        bas_raise(E_DUPLICATE_DEF);
    if (ndims < 1 || ndims > BAS_MAX_DIMS)
        bas_raise(E_SUBSCRIPT);
    for (i = 0; i < ndims; i++) {
        if (counts[i] < 1)
            bas_raise(E_SUBSCRIPT);
        if (total > 0x4000000 / counts[i])
            bas_raise(E_OUT_OF_MEMORY);
        total *= counts[i];
    }
    a = bas_alloc((uint32_t)sizeof(BasArray) + (uint32_t)total * (uint32_t)sizeof(BasValue));
    if (!a)
        bas_raise(E_OUT_OF_MEMORY);
    a->type  = var->type;
    a->ndims = (uint8_t)ndims;
    a->total = total;
    for (i = 0; i < BAS_MAX_DIMS; i++)
        a->count[i] = (i < ndims) ? counts[i] : 0;
    for (i = 0; i < total; i++) {
        a->data[i].type = a->type;
        a->data[i].v.d  = 0.0;
        if (a->type == T_STR)
            a->data[i].v.s = 0;
    }
    var->is_array = 1;
    var->array = a;
    return a;
}

/* ------------------------------------------------------------ lexing helpers */

int bas_is_name_start(int c)
{
    return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z');
}

int bas_is_name_char(int c)
{
    return bas_is_name_start(c) || (c >= '0' && c <= '9') || c == '.';
}

void bas_skip_spaces(const char **p, const char *end)
{
    while (*p < end && (**p == ' ' || **p == '\t'))
        (*p)++;
}

int bas_upcase(int c)
{
    return (c >= 'a' && c <= 'z') ? c - 32 : c;
}

int bas_keyword_at(const char *p, const char *end, const char *word)
{
    while (*word) {
        if (p >= end || bas_upcase((uint8_t)*p) != (uint8_t)*word)
            return 0;
        p++;
        word++;
    }
    return !(p < end && bas_is_name_char((uint8_t)*p));
}

int bas_keyword(const char **p, const char *end, const char *word)
{
    const char *q = *p;
    const char *w = word;

    if (!bas_keyword_at(q, end, word))
        return 0;
    while (*w) {
        q++;
        w++;
    }
    *p = q;
    return 1;
}

int bas_read_name(const char **p, const char *end, char out[16])
{
    const char *q = *p;
    int         n = 0;

    if (q >= end || !bas_is_name_start((uint8_t)*q))
        return 0;
    while (q < end && bas_is_name_char((uint8_t)*q)) {
        if (n < 14)
            out[n++] = (char)bas_upcase((uint8_t)*q);
        q++;
    }
    if (q < end && suffix_type((uint8_t)*q))
        out[n++] = *q++;
    out[n] = 0;
    *p = q;
    return 1;
}

void bas_upper_line(char *text, int len)
{
    int i = 0;
    int at_stmt = 1;                /* start of a statement (or after ':') */
    int raw = 0;                    /* inside REM / DATA text */
    int in_quotes = 0;

    while (i < len) {
        char c = text[i];
        if (c == '\0')
            break;
        if (raw) {
            if (c == '"')
                in_quotes = !in_quotes;
            else if (c == ':' && !in_quotes) {
                raw = 0;
                at_stmt = 1;
            }
            i++;
            continue;
        }
        if (c == '"') {
            i++;
            while (i < len && text[i] != '"')
                i++;
            if (i < len)
                i++;
            at_stmt = 0;
            continue;
        }
        if (c == ':') {
            at_stmt = 1;
            i++;
            continue;
        }
        if (at_stmt && c == '\'')
            return;                 /* comment: keep the text exactly as typed */
        if (at_stmt && bas_keyword_at(text + i, text + len, "REM")) {
            text[i] = 'R';
            text[i + 1] = 'E';
            text[i + 2] = 'M';
            return;                 /* rest of the line is comment text */
        }
        if (at_stmt && bas_keyword_at(text + i, text + len, "DATA")) {
            text[i] = 'D';
            text[i + 1] = 'A';
            text[i + 2] = 'T';
            text[i + 3] = 'A';
            i += 4;
            raw = 1;
            at_stmt = 0;
            continue;
        }
        text[i] = (char)bas_upcase((uint8_t)c);
        if (c != ' ' && c != '\t')
            at_stmt = 0;
        i++;
    }
}

/* --------------------------------------------------------- numeric literals */

static const double pow10_tab[23] = {
    1e0,  1e1,  1e2,  1e3,  1e4,  1e5,  1e6,  1e7,  1e8,  1e9,  1e10, 1e11,
    1e12, 1e13, 1e14, 1e15, 1e16, 1e17, 1e18, 1e19, 1e20, 1e21, 1e22
};

static double scale10(double d, int e)
{
    if (d == 0.0)
        return d;
    while (e > 22) {
        d *= 1e22;
        e -= 22;
        if (d > 1e300)
            break;
    }
    while (e < -22) {
        d *= 1e-22;
        e += 22;
    }
    if (e > 0)
        d *= pow10_tab[e];
    else if (e < 0)
        d /= pow10_tab[-e];
    return d;
}

int bas_scan_number(const char **p, const char *end, BasValue *out)
{
    const char *q = *p;
    const char *start = q;
    uint64_t    mant = 0;
    int         in_mant = 0;
    int         started = 0;        /* seen a non-zero digit */
    int         sig_digits = 0;
    int         int_digits = 0;
    int         exp10 = 0;
    int         seen_dot = 0;
    int         exp_used = 0;
    int         exp_is_d = 0;
    int         force = 0;
    int         type;

    while (q < end && *q >= '0' && *q <= '9') {
        int d = *q - '0';

        int_digits++;
        if (d != 0 || started) {
            started = 1;
            sig_digits++;
            if (in_mant < 19) {
                mant = mant * 10u + (uint64_t)d;
                in_mant++;
            } else {
                exp10++;
            }
        }
        q++;
    }
    if (q < end && *q == '.') {
        seen_dot = 1;
        q++;
        while (q < end && *q >= '0' && *q <= '9') {
            int d = *q - '0';

            if (d != 0 || started) {
                started = 1;
                sig_digits++;
                if (in_mant < 19) {
                    mant = mant * 10u + (uint64_t)d;
                    in_mant++;
                    exp10--;
                }
            } else {
                exp10--;
            }
            q++;
        }
    }
    if (!started && int_digits == 0 && !seen_dot)
        return 0;                   /* no digits at all: not a number */
    if (q < end && (*q == 'E' || *q == 'e' || *q == 'D' || *q == 'd')) {
        const char *save = q;
        int         esign = 1;
        int         ev = 0;
        int         edig = 0;
        int         is_d = (*q == 'D' || *q == 'd');

        q++;
        if (q < end && (*q == '+' || *q == '-')) {
            if (*q == '-')
                esign = -1;
            q++;
        }
        while (q < end && *q >= '0' && *q <= '9') {
            if (ev < 100000)
                ev = ev * 10 + (*q - '0');
            edig++;
            q++;
        }
        if (edig == 0) {
            q = save;
        } else {
            exp10 += esign * ev;
            exp_used = 1;
            exp_is_d = is_d;
        }
    }
    if (q < end) {
        int t = suffix_type((uint8_t)*q);

        if (t) {
            force = t;
            q++;
        }
    }
    if (q == start)
        return 0;
    if (force)
        type = force;
    else if (seen_dot || exp_used)
        type = (exp_is_d || sig_digits > 7) ? T_DBL : T_SNG;
    else if (sig_digits <= 5 && mant <= 32767u)
        type = T_INT;
    else
        type = T_SNG;

    if (type == T_INT) {
        if (exp10 != 0 || mant > 32767u)
            bas_raise(E_OVERFLOW);
        out->type = T_INT;
        out->v.i = (int32_t)mant;
    } else {
        double d = (double)mant;

        d = scale10(d, exp10);
        if (type == T_SNG) {
            if (d > 3.4028234663852886e38 || d < -3.4028234663852886e38)
                bas_raise(E_OVERFLOW);
            out->type = T_SNG;
            out->v.f = (float)d;
        } else {
            out->type = T_DBL;
            out->v.d = d;
        }
    }
    *p = q;
    return 1;
}
