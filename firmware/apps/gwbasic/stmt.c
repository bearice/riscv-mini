/* stmt.c - statement execution for the payload-side GW-BASIC interpreter.
 *
 * Control flow is driven by the line list: a jump resolves a line number to a
 * BasLine and execution restarts at the beginning of that line.  A GOSUB or a
 * FOR remembers the (line, offset) pair just after the statement so that
 * RETURN and NEXT can resume in the middle of a line. */
#include "basic.h"
#include <string.h>

int bas_running;
int bas_trace;
int bas_break;
int bas_cont_valid;

/* ------------------------------------------------------------------- stacks */

typedef struct GosubFrame {
    int line;                       /* line number to return to */
    int offset;                     /* character offset inside that line */
} GosubFrame;

typedef struct ForFrame {
    BasVar   *var;
    BasValue  limit;
    BasValue  step;
    int       line;                 /* line containing the FOR */
    int       offset;               /* just after the FOR statement */
} ForFrame;

typedef struct WhileFrame {
    int line;
    int offset;                     /* start of the WHILE condition */
} WhileFrame;

static GosubFrame gosub_stack[BAS_MAX_GOSUB];
static int        gosub_depth;
static ForFrame   for_stack[BAS_MAX_FOR];
static int        for_depth;
static WhileFrame while_stack[BAS_MAX_WHILE];
static int        while_depth;

/* ON ERROR handler and the state needed by RESUME. */
static int err_handler_line;
static int err_handler_active;
static int resume_line;
static int resume_offset;
static int resume_valid;

/* DATA scanning: one entry per DATA keyword, in program order. */
#define MAX_DATA 256
static struct { BasLine *line; int offset; } data_refs[MAX_DATA];
static int      data_count;
static int      data_index;         /* index into data_refs */
static int      data_offset;        /* offset of the next item, 0 = statement start */

/* CONT state. */
static int cont_line;
static int cont_offset;

/* The line currently being executed; bas_current is the same pointer but the
 * offset is needed for the resume points. */
static int exec_offset;

/* Shorthands used all over the statement layer. */
#define bas_current_line  ((int)(bas_current ? bas_current->number : 0))
#define bas_line_text     (bas_current ? (const char *)bas_current->text : (const char *)0)

/* Last LINE/PSET endpoint, so that LINE -(x,y) can continue from it. */
int bas_last_x;
int bas_last_y;

void bas_gosub_push(int line, int offset)
{
    if (gosub_depth >= BAS_MAX_GOSUB)
        bas_raise(E_OUT_OF_MEMORY);
    gosub_stack[gosub_depth].line = line;
    gosub_stack[gosub_depth].offset = offset;
    gosub_depth++;
}

static void for_push(BasVar *var, BasValue limit, BasValue step, int line, int offset)
{
    if (for_depth >= BAS_MAX_FOR)
        bas_raise(E_OUT_OF_MEMORY);
    for_stack[for_depth].var = var;
    for_stack[for_depth].limit = limit;
    for_stack[for_depth].step = step;
    for_stack[for_depth].line = line;
    for_stack[for_depth].offset = offset;
    for_depth++;
}

static void stacks_clear(void)
{
    int i;

    for (i = 0; i < for_depth; i++) {
        val_release(&for_stack[i].limit);
        val_release(&for_stack[i].step);
    }
    gosub_depth = 0;
    for_depth = 0;
    while_depth = 0;
    err_handler_active = 0;
    resume_valid = 0;
}

void bas_for_reset(void)
{
    stacks_clear();
}

/* -------------------------------------------------------------------- DATA */
/* Every DATA keyword in the program is recorded as (line, offset) before a run
 * starts; READ walks that list.  DATA text ends at the next ':' that is outside
 * a string, not at the end of the line. */
static void data_scan(void)
{
    BasLine *l = bas_program;

    data_count = 0;
    data_index = 0;
    data_offset = 0;
    while (l) {
        const char *p = l->text;
        const char *end = l->text + l->len;
        int         in_quotes = 0;

        while (p < end) {
            if (*p == '"') {
                in_quotes = !in_quotes;
                p++;
                continue;
            }
            if (!in_quotes && bas_keyword_at(p, end, "DATA")) {
                if (data_count < MAX_DATA) {
                    data_refs[data_count].line = l;
                    data_refs[data_count].offset = (int)(p - l->text) + 4;
                    data_count++;
                }
                p += 4;
                continue;
            }
            p++;
        }
        l = l->next;
    }
}

void bas_data_reset(void)
{
    data_index = 0;
    data_offset = 0;
}

/* The DATA list is built from the program text, so editing the program has to
 * drop it. */
void data_scan_reset(void)
{
    data_count = 0;
    data_index = 0;
    data_offset = 0;
}

/* Read one DATA item.  Unquoted items stop at ',' or ':'; quoted items keep
 * their text and may contain either. */
static BasValue data_item(void)
{
    for (;;) {
        BasLine    *l;
        const char *p, *end;
        BasValue    v;

        if (data_index >= data_count)
            bas_raise(E_OUT_OF_DATA);
        l = data_refs[data_index].line;
        p = l->text + (data_offset ? data_offset : data_refs[data_index].offset);
        end = l->text + l->len;
        bas_skip_spaces(&p, end);
        if (p >= end || *p == ':') {     /* this DATA statement is used up */
            data_index++;
            data_offset = 0;
            continue;
        }
        if (*p == '"') {
            const char *s = ++p;
            int         len = 0;

            while (p < end && *p != '"') {
                p++;
                len++;
            }
            if (p >= end)
                bas_raise(E_OUT_OF_DATA);
            p++;
            v.type = T_STR;
            v.v.s = str_from(s, (uint32_t)len);
        } else {
            const char *s = p;
            int          len = 0;

            while (p < end && *p != ',' && *p != ':') {
                p++;
                len++;
            }
            while (len > 0 && (s[len - 1] == ' ' || s[len - 1] == '\t'))
                len--;
            {
                const char *q = s;

                if (!bas_scan_number(&q, s + len, &v) || q != s + len) {
                    if (len == 0)
                        bas_raise(E_OUT_OF_DATA);
                    v.type = T_STR;
                    v.v.s = str_from(s, (uint32_t)len);
                }
            }
        }
        if (p < end && *p == ',') {
            data_offset = (int)(p - l->text) + 1;
        } else {
            data_index++;
            data_offset = 0;
        }
        return v;
    }
}
/* ------------------------------------------------------------- assignment */

static void assign_to(BasValue *slot, BasValue value)
{
    if (slot->type == T_STR) {
        if (value.type != T_STR) {
            val_release(&value);
            bas_raise(E_TYPE_MISMATCH);
        }
        str_release(slot->v.s);
        *slot = value;
        return;
    }
    if (value.type == T_STR) {
        val_release(&value);
        bas_raise(E_TYPE_MISMATCH);
    }
    val_promote(&value, slot->type);
    slot->v = value.v;
    val_release(&value);
}

/* Lvalues: a variable, an array element, or a MID$()/LEFT$()/RIGHT$() slice. */
static BasValue *lvalue(const char **p, const char *end, int *is_slice,
                        BasValue *slice_val, int *slice_start, int *slice_len)
{
    char name[16];
    const char *q = *p;

    *is_slice = 0;
    if (!bas_read_name(&q, end, name))
        bas_raise(E_SYNTAX);
    {
        const BasBuiltin *b = bas_builtin_find(name);

        if (b) {
            if (bas_builtin_take(b, 0)) {
                *p = q;
                return 0;           /* INKEY$ = ... : discard the value */
            }
            if (strcmp(name, "MID$") == 0 || strcmp(name, "LEFT$") == 0 ||
                strcmp(name, "RIGHT$") == 0) {
                BasValue args[3];
                int      nargs = 0;
                BasValue *slot;

                q++;                /* '(' */
                while (nargs < 3) {
                    bas_skip_spaces(&q, end);
                    args[nargs++] = bas_expr(&q, end);
                    bas_skip_spaces(&q, end);
                    if (q < end && *q == ',') {
                        q++;
                        continue;
                    }
                    break;
                }
                if (q >= end || *q != ')') {
                    int i;

                    for (i = 0; i < nargs; i++)
                        val_release(&args[i]);
                    bas_raise(E_SYNTAX);
                }
                q++;
                if (nargs < 2)
                    bas_raise(E_ILLEGAL_FUNCTION_CALL);
                slot = lvalue(&q, end, is_slice, slice_val, slice_start, slice_len);
                if (!slot || slot->type != T_STR)
                    bas_raise(E_ILLEGAL_FUNCTION_CALL);
                *p = q;
                /* The slice parameters are kept for the assignment step. */
                *slice_val = args[0];
                *slice_start = val_integer(&args[1]);
                *slice_len = (nargs >= 3) ? val_integer(&args[2]) : -1;
                {
                    int i;

                    for (i = 2; i < nargs; i++)
                        val_release(&args[i]);
                }
                return slot;
            }
            bas_raise(E_ILLEGAL_FUNCTION_CALL);
        }
        {
            BasVar *var = bas_var(name, (int)strlen(name), 1);

            bas_skip_spaces(&q, end);
            if (q < end && *q == '(') {
                BasValue *slot;

                q++;
                if (!var->is_array && var->used)
                    bas_raise(E_DUPLICATE_DEF);   /* was a scalar */
                var->used = 1;
                slot = 0;
                {
                    /* Reuse the array indexing logic from expr.c through a
                     * local copy: keep it here so lvalues stay cheap. */
                    BasArray *a = var->array;
                    int32_t   sub[BAS_MAX_DIMS];
                    int       n = 0;
                    int32_t   index = 0, stride;

                    if (!a) {
                        int32_t counts[1];

                        counts[0] = 11;
                        a = bas_array_dim(var, 1, counts);
                    }
                    for (;;) {
                        BasValue v = bas_expr(&q, end);
                        int32_t  iv = 0;

                        if (v.type == T_STR) {
                            val_release(&v);
                            bas_raise(E_TYPE_MISMATCH);
                        }
                        iv = val_int_of(&v);
                        val_release(&v);
                        if (n >= BAS_MAX_DIMS)
                            bas_raise(E_SUBSCRIPT);
                        sub[n++] = iv;
                        bas_skip_spaces(&q, end);
                        if (q < end && *q == ',') {
                            q++;
                            if (n >= (int)a->ndims)
                                bas_raise(E_SUBSCRIPT);
                            continue;
                        }
                        break;
                    }
                    if (q >= end || *q != ')')
                        bas_raise(E_SYNTAX);
                    q++;
                    if (n != (int)a->ndims)
                        bas_raise(E_SUBSCRIPT);
                    stride = 1;
                    {
                        int i;

                        for (i = a->ndims - 1; i >= 0; i--) {
                            if (sub[i] < 0 || sub[i] >= a->count[i])
                                bas_raise(E_SUBSCRIPT);
                            index += sub[i] * stride;
                            stride *= a->count[i];
                        }
                    }
                    slot = &a->data[index];
                }
                *p = q;
                return slot;
            }
            if (var->is_array)
                bas_raise(E_ILLEGAL_FUNCTION_CALL);
            var->used = 1;
            *p = q;
            return &var->value;
        }
    }
}

static void assign_slice(BasValue *slot, BasValue value, int start, int len)
{
    uint32_t   target_len = str_len(slot->v.s);
    const char *src = str_ptr(value.v.s);
    uint32_t   src_len = str_len(value.v.s);
    BasStr    *out;
    uint32_t   i;

    if (start < 1)
        bas_raise(E_ILLEGAL_FUNCTION_CALL);
    if (len < 0)
        len = (int)src_len;
    if (len > (int)src_len)
        len = (int)src_len;
    if ((uint32_t)(start - 1) + (uint32_t)len > target_len) {
        /* LEFT$()/RIGHT$() cannot grow a string; MID$() can. */
        target_len = (uint32_t)(start - 1) + (uint32_t)len;
    }
    out = str_alloc(target_len);
    for (i = 0; i < target_len; i++)
        out->data[i] = (i < str_len(slot->v.s)) ? slot->v.s->data[i] : ' ';
    for (i = 0; i < (uint32_t)len; i++)
        out->data[(uint32_t)(start - 1) + i] = src[i];
    str_release(slot->v.s);
    slot->type = T_STR;
    slot->v.s = out;
}

/* ------------------------------------------------------------------- PRINT */

#define PRINT_ZONE 14

static void print_string(const char *s, unsigned n)
{
    con_write(s, n);
    bas_note_output(s, n);
}

static void print_pad(int columns)
{
    while (columns-- > 0) {
        con_putc(' ');
        bas_note_output(" ", 1);
    }
}

static void print_zone_pad(void)
{
    int pos = bas_pos_value();
    int next = ((pos / PRINT_ZONE) + 1) * PRINT_ZONE;

    if (next > 79)
        next = 0;
    if (next == 0) {
        con_newline();
        bas_note_output("\n", 1);
    } else {
        print_pad(next - pos);
    }
}

/* One field of a PRINT USING image.  Emits the field for one value. */
static void print_using_field(const char *fmt, int start, int stop, const BasValue *v)
{
    char   image[128];
    char   num[48];
    int    out = 0;
    int    i;
    int    flags = 0;               /* 1 sign forced, 2 leading stars, 4 dollars */
    int    left = 0;                /* '!' image */
    int    width;
    int    digits, dec, nlen, j;

    for (i = start; i < stop; i++) {
        char c = fmt[i];

        if (c == '_' && i + 1 < stop) {
            image[out++] = fmt[++i];
            continue;
        }
        if (c == '\\') {
            /* \<...\> : the number is inserted verbatim between the markers. */
            int j2 = i + 1;

            while (j2 < stop && fmt[j2] != '\\')
                j2++;
            if (j2 >= stop)
                bas_raise(E_ILLEGAL_FUNCTION_CALL);
            bas_format_number(num, sizeof(num), v);
            print_string(image, (unsigned)out);
            print_string(num, (unsigned)strlen(num));
            print_string(fmt + i + 1, (unsigned)(j2 - i - 1));
            return;
        }
        if (c == '!') {
            left = 1;
            continue;
        }
        if (c == '&')
            continue;
        if (c == '#' || c == '.' || c == ',' || c == '+' || c == '-' ||
            c == '$' || c == '*' || c == '%' || c == '^') {
            image[out++] = c;
            if (c == '+')
                flags |= 1;
            else if (c == '$')
                flags |= 4;
            else if (c == '*')
                flags |= 2;
            continue;
        }
        image[out++] = c;
    }
    image[out] = 0;
    width = out;
    if (width <= 0 || width >= (int)sizeof(image))
        bas_raise(E_ILLEGAL_FUNCTION_CALL);

    if (v->type == T_STR) {
        const char *s = str_ptr(v->v.s);
        uint32_t    len = str_len(v->v.s);

        if ((int)len > width)
            len = (uint32_t)width;
        if (left) {
            print_string(s, len);
            print_pad(width - (int)len);
        } else {
            print_pad(width - (int)len);
            print_string(s, len);
        }
        return;
    }

    /* Count the digit positions the image allows. */
    {
        int hash = 0, dot_at = -1, after = 0;

        for (j = 0; j < out; j++) {
            if (image[j] == '#')
                hash++;
            else if (image[j] == '.')
                dot_at = j;
        }
        if (dot_at >= 0) {
            for (j = dot_at + 1; j < out; j++)
                if (image[j] == '#')
                    after++;
            dec = after;
            digits = hash - after;
        } else {
            dec = 0;
            digits = hash;
        }
    }
    /* The image length is the field width; the value is right aligned in it. */
    {
        int field = digits + (dec ? dec + 1 : 0);

        nlen = bas_format_using(num, sizeof(num), v, digits, dec, flags);
        if (nlen > field) {
            /* GW-BASIC marks a value that will not fit with a leading '%'. */
            print_string("%", 1);
            nlen = bas_format_using(num, sizeof(num), v, 18, dec, flags);
        }
        print_string(num, (unsigned)nlen);
    }
}

/* PRINT [USING fmt;] expr [,{expr}*] [;|,] */
static void do_print(const char **p, const char *end)
{
    const char *q = *p;
    int      trailing = 0;           /* 1 suppresses the final newline */
    BasValue using;

    using.type = 0;
    bas_skip_spaces(&q, end);
    if (q < end && bas_keyword(&q, end, "USING")) {
        bas_skip_spaces(&q, end);
        using = bas_expr(&q, end);
        if (using.type != T_STR)
            bas_raise(E_TYPE_MISMATCH);
        bas_skip_spaces(&q, end);
        if (q >= end || *q != ';')
            bas_raise(E_SYNTAX);
        q++;
    }
    for (;;) {
        BasValue v;

        bas_skip_spaces(&q, end);
        if (q >= end || *q == ':')      /* ':' ends the PRINT, not a new field */
            break;
        if (*q == ';') {
            q++;
            trailing = 1;
            continue;
        }
        if (*q == ',') {
            q++;
            print_zone_pad();
            trailing = 1;            /* a trailing comma also holds the cursor */
            continue;
        }
        if (*q == '"') {
            const char *s = ++q;
            int         len = 0;

            while (q < end && *q != '"') {
                q++;
                len++;
            }
            if (q >= end)
                bas_raise(E_SYNTAX);
            q++;
            trailing = 0;
            print_string(s, (unsigned)len);
            bas_skip_spaces(&q, end);
            if (q < end && (*q == ';' || *q == ','))
                continue;
            break;
        }
        trailing = 0;                /* printing a value clears the hold */
        v = bas_expr(&q, end);
        if (using.type) {
            const char *fmt = str_ptr(using.v.s);
            uint32_t    flen = str_len(using.v.s);
            uint32_t    at = 0;
            int         start = -1, i;

            /* Find the next image in the format string. */
            for (i = 0; i < (int)flen; i++) {
                char c = fmt[i];

                if (c == '_' && i + 1 < (int)flen) {
                    i++;
                    continue;
                }
                if (c == '#' || c == '!' || c == '&' || c == '\\') {
                    start = i;
                    break;
                }
            }
            if (start < 0) {
                print_string(fmt, flen);
            } else {
                int stop = start;

                if (fmt[start] == '\\') {
                    while (stop < (int)flen && fmt[stop] != '\\')
                        stop++;
                    stop++;
                } else if (fmt[start] == '!') {
                    stop = start + 1;
                } else if (fmt[start] == '&') {
                    stop = start + 1;
                    if (stop < (int)flen && fmt[stop] == '&')
                        stop++;
                } else {
                    while (stop < (int)flen) {
                        char c = fmt[stop];

                        if (c == '#' || c == '.' || c == ',' || c == '+' ||
                            c == '-' || c == '$' || c == '*' || c == '^' ||
                            c == '%') {
                            stop++;
                            continue;
                        }
                        if (c == '_' && stop + 1 < (int)flen) {
                            stop += 2;
                            continue;
                        }
                        break;
                    }
                }
                if (start > 0)
                    print_string(fmt, (unsigned)start);
                print_using_field(fmt, start, stop, &v);
                /* Keep the remainder of the image for the next field. */
                {
                    BasStr *rest = str_alloc(flen - (uint32_t)stop);

                    memcpy(rest->data, fmt + stop, flen - (uint32_t)stop);
                    str_release(using.v.s);
                    using.v.s = rest;
                }
            }
            (void)at;
        } else if (v.type == T_STR) {
            print_string(str_ptr(v.v.s), str_len(v.v.s));
        } else {
            bas_print_number(&v);
        }
        val_release(&v);
        bas_skip_spaces(&q, end);
        if (q < end && (*q == ';' || *q == ','))
            continue;
        break;
    }
    if (using.type)
        val_release(&using);
    if (!trailing)
        con_newline();
    *p = q;
}

/* -------------------------------------------------------------------- INPUT */

static void input_prompt(const char **p, const char *end, int *have_prompt,
                         char *prompt, int *prompt_len)
{
    const char *q = *p;

    *have_prompt = 0;
    bas_skip_spaces(&q, end);
    if (q < end && *q == '"') {
        const char *s = ++q;
        int         len = 0;

        while (q < end && *q != '"') {
            q++;
            len++;
        }
        if (q >= end)
            bas_raise(E_SYNTAX);
        q++;
        if ((uint32_t)len > BAS_LINE_MAX - 1)
            len = (int)BAS_LINE_MAX - 1;
        memcpy(prompt, s, (unsigned)len);
        prompt[len] = 0;
        *prompt_len = len;
        *have_prompt = 1;
        bas_skip_spaces(&q, end);
        if (q >= end || (*q != ';' && *q != ','))
            bas_raise(E_SYNTAX);
        q++;
        *p = q;
    }
}

/* Read one comma-separated field from a buffered line. */
static BasValue parse_field(const char **pp, const char *end, int want_string)
{
    const char *p = *pp;
    BasValue    v;

    bas_skip_spaces(&p, end);
    if (p >= end) {
        *pp = p;
        return want_string ? val_empty() : val_sng(0.0f);
    }
    if (*p == '"') {
        const char *s = ++p;
        int         len = 0;

        while (p < end && *p != '"') {
            p++;
            len++;
        }
        if (p >= end)
            bas_raise(E_SYNTAX);
        p++;
        *pp = p;
        v.type = T_STR;
        v.v.s = str_from(s, (uint32_t)len);
        return v;
    }
    if (want_string) {
        const char *s = p;
        int         len = 0;

        while (p < end && *p != ',') {
            p++;
            len++;
        }
        while (len > 0 && (s[len - 1] == ' ' || s[len - 1] == '\t'))
            len--;
        while (len > 0 && (*s == ' ' || *s == '\t')) {
            s++;
            len--;
        }
        *pp = p;
        v.type = T_STR;
        v.v.s = str_from(s, (uint32_t)len);
        return v;
    }
    {
        const char *s = p;
        int         len = 0;

        while (p < end && *p != ',') {
            p++;
            len++;
        }
        while (len > 0 && (s[len - 1] == ' ' || s[len - 1] == '\t'))
            len--;
        {
            const char *q = s;

            if (!bas_scan_number(&q, s + len, &v) || q != s + len) {
                v.type = T_STR;
                v.v.s = str_from(s, (uint32_t)len);
                if (!want_string)
                    bas_raise(E_TYPE_MISMATCH);
            }
        }
        *pp = p;
        return v;
    }
}

static void read_input_line(char *buf, int *len)
{
    int c;

    *len = 0;
    for (;;) {
        c = con_getc_wait();
        if (c < 0 || c == '\n' || c == '\r')
            break;
        if (c == '\b') {
            if (*len > 0) {
                (*len)--;
                con_puts("\b \b");
            }
            continue;
        }
        if ((uint32_t)*len < BAS_LINE_MAX - 1) {
            buf[(*len)++] = (char)c;
            con_putc((char)c);
        }
    }
    buf[*len] = 0;
    con_newline();
}

static void do_input(const char **p, const char *end)
{
    char        prompt[BAS_LINE_MAX];
    int         prompt_len = 0, have_prompt;
    const char *q = *p;
    char        line[BAS_LINE_MAX];
    int         llen;
    const char *lp, *lend;
    int         first = 1;

    input_prompt(&q, end, &have_prompt, prompt, &prompt_len);
    for (;;) {
        BasValue *slot;
        int      is_slice, start, len;
        BasValue slice;

        bas_skip_spaces(&q, end);
        if (q >= end || *q == ':')
            break;
        if (!first) {
            if (q < end && *q == ',') {
                q++;
            } else {
                break;
            }
        }
        first = 0;
        slice.type = 0;
        slot = lvalue(&q, end, &is_slice, &slice, &start, &len);
        if (!slot)
            bas_raise(E_ILLEGAL_FUNCTION_CALL);
        if (slot->type != T_STR && slot->type != T_INT && slot->type != T_SNG &&
            slot->type != T_DBL)
            bas_raise(E_TYPE_MISMATCH);
        /* One prompt per INPUT, then read a whole line. */
        if (have_prompt) {
            print_string(prompt, (unsigned)prompt_len);
            con_flush();
        }
        print_string("?", 1);
        con_putc(' ');
        con_flush();
        read_input_line(line, &llen);
        lp = line;
        lend = line + llen;
        {
            BasValue v = parse_field(&lp, lend, slot->type == T_STR);

            if (is_slice) {
                if (v.type != T_STR) {
                    val_release(&v);
                    bas_raise(E_TYPE_MISMATCH);
                }
                assign_slice(slot, v, start, len);
                val_release(&v);
            } else {
                assign_to(slot, v);
            }
        }
        if (slot->type == T_STR) {
            /* A string variable accepts the rest of the line as-is. */
        }
        if (is_slice && slice.type)
            val_release(&slice);
    }
    *p = q;
}

static void do_line_input(const char **p, const char *end)
{
    char        prompt[BAS_LINE_MAX];
    int         prompt_len = 0, have_prompt;
    const char *q = *p;
    char        line[BAS_LINE_MAX];
    int         llen;
    BasValue   *slot;
    int         is_slice, start, len;
    BasValue    slice;

    input_prompt(&q, end, &have_prompt, prompt, &prompt_len);
    if (have_prompt) {
        print_string(prompt, (unsigned)prompt_len);
        con_flush();
    }
    read_input_line(line, &llen);
    slice.type = 0;
    slot = lvalue(&q, end, &is_slice, &slice, &start, &len);
    if (!slot || slot->type != T_STR)
        bas_raise(E_TYPE_MISMATCH);
    {
        BasValue v = val_str(str_from(line, (uint32_t)llen));

        if (is_slice) {
            assign_slice(slot, v, start, len);
            val_release(&v);
        } else {
            assign_to(slot, v);
        }
    }
    if (slice.type)
        val_release(&slice);
    *p = q;
}

/* ------------------------------------------------------------------ control */

static BasLine *find_line_or_die(int number)
{
    BasLine *l = bas_line_find(number);

    if (!l)
        bas_raise_line(E_UNDEFINED_LINE, number);
    return l;
}

/* Skip to the ELSE that belongs to the IF whose condition just failed.  The
 * scan counts nested IF/THEN pairs so that the ELSE binds to the right IF. */
static int skip_to_else(const char **p, const char *end)
{
    const char *q = *p;
    int        depth = 1;
    int        in_quotes = 0;

    while (q < end) {
        if (*q == '"') {
            in_quotes = !in_quotes;
            q++;
            continue;
        }
        if (in_quotes) {
            q++;
            continue;
        }
        if (bas_keyword_at(q, end, "IF")) {
            depth++;
            q += 2;
            continue;
        }
        if (bas_keyword_at(q, end, "ELSE")) {
            depth--;
            if (depth == 0) {
                *p = q + 4;
                return 1;
            }
            q += 4;
            continue;
        }
        q++;
    }
    *p = q;
    return 0;
}

static void do_if(const char **p, const char *end)
{
    const char *q = *p;
    BasValue    cond;
    int         truth;

    cond = bas_expr(&q, end);
    truth = val_truth(&cond);
    val_release(&cond);
    bas_skip_spaces(&q, end);
    if (q < end && bas_keyword(&q, end, "GOTO")) {
        BasValue t = bas_expr(&q, end);
        int      target = val_integer(&t);

        val_release(&t);
        if (truth) {
            *p = q;
            bas_jump_line(target);
            return;
        }
        /* False: the rest of the line is skipped, but an ELSE still runs. */
        if (skip_to_else(&q, end)) {
            *p = q;
            return;
        }
        *p = q;
        return;
    }
    if (q >= end || !bas_keyword(&q, end, "THEN"))
        bas_raise(E_SYNTAX);
    if (!truth) {
        if (skip_to_else(&q, end)) {
            /* The ELSE branch runs to the end of the line. */
            *p = q;
            return;
        }
        *p = q;
        return;
    }
    /* True branch: run statements until the matching ELSE or the end. */
    *p = q;
    {
        int stop = bas_run_sequence(&q, end, 1);

        if (stop == 1) {
            /* Reached our ELSE: skip the false branch. */
            while (q < end && *q != ':') {
                if (*q == '"') {
                    q++;
                    while (q < end && *q != '"')
                        q++;
                }
                if (q < end && *q != ':')
                    q++;
            }
            if (q < end)
                q++;
            *p = q;
            return;
        }
        *p = q;
    }
}

/* Execute statements separated by ':' until the end of the line.  Returns 1 if
 * an ELSE was reached (inside an IF true-branch), 0 at the end of the line. */
int bas_run_sequence(const char **p, const char *end, int in_if)
{
    const char *q = *p;

    for (;;) {
        bas_skip_spaces(&q, end);
        if (q >= end)
            break;
        if (in_if && bas_keyword_at(q, end, "ELSE")) {
            *p = q;
            return 1;
        }
        if (*q == ':') {
            q++;
            continue;
        }
        bas_exec_statement(&q, end);
        if (bas_jump_pending) {
            *p = q;
            return 2;
        }
    }
    *p = q;
    return 0;
}

/* ---------------------------------------------------------------- FOR/NEXT */

static void do_for(const char **p, const char *end)
{
    const char *q = *p;
    char        name[16];
    BasVar     *var;
    BasValue    start, limit, step;
    int         after;

    bas_skip_spaces(&q, end);
    if (!bas_read_name(&q, end, name))
        bas_raise(E_SYNTAX);
    var = bas_var(name, (int)strlen(name), 1);
    if (var->is_array)
        bas_raise(E_ILLEGAL_FUNCTION_CALL);
    if (var->value.type == T_STR)
        bas_raise(E_TYPE_MISMATCH);
    bas_skip_spaces(&q, end);
    if (q >= end || *q != '=')
        bas_raise(E_SYNTAX);
    q++;
    start = bas_expr(&q, end);
    val_promote(&start, var->value.type);
    var->used = 1;
    var->value = start;
    bas_skip_spaces(&q, end);
    if (q >= end || !bas_keyword(&q, end, "TO"))
        bas_raise(E_SYNTAX);
    limit = bas_expr(&q, end);
    val_promote(&limit, start.type);
    step = val_sng(1.0f);
    bas_skip_spaces(&q, end);
    if (q < end && bas_keyword(&q, end, "STEP")) {
        val_release(&step);
        step = bas_expr(&q, end);
        val_promote(&step, start.type);
    }
    after = (int)(q - (const char *)bas_current->text);
    for_push(var, limit, step, (int)bas_current->number, after);
    *p = q;
}

static void do_next(const char **p, const char *end)
{
    const char *q = *p;
    ForFrame   *f;
    BasValue    nv;
    int         done;

    if (for_depth == 0)
        bas_raise(E_NEXT_WITHOUT_FOR);
    f = &for_stack[for_depth - 1];
    if (q < end) {
        char name[16];
        const char *save = q;

        bas_skip_spaces(&q, end);
        if (bas_read_name(&q, end, name)) {
            BasVar *var = bas_var(name, (int)strlen(name), 1);

            if (var != f->var)
                bas_raise(E_NEXT_WITHOUT_FOR);
        } else {
            q = save;
        }
    }
    nv = bas_arith(OP_ADD, &f->var->value, &f->step);
    assign_to(&f->var->value, nv);
    {
        int cmp = val_compare(&f->var->value, &f->limit);
        int positive = val_truth(&f->step);

        done = positive ? (cmp > 0) : (cmp < 0);
    }
    if (done) {
        val_release(&f->limit);
        val_release(&f->step);
        for_depth--;
        *p = q;
        return;
    }
    /* Loop back to the statement after the FOR.  The saved offset is just past
     * the FOR statement, so the loop body starts there. */
    bas_jump_line_offset(f->line, f->offset);
    *p = q;
}

static void do_while(const char **p, const char *end)
{
    const char *q = *p;
    BasValue    cond;
    int         truth;

    if (while_depth >= BAS_MAX_WHILE)
        bas_raise(E_OUT_OF_MEMORY);
    while_stack[while_depth].line = (int)bas_current->number;
    /* WEND has to re-execute the whole WHILE statement, so the frame records
     * the start of the statement, not the position of its condition. */
    while_stack[while_depth].offset = exec_offset;
    while_depth++;
    cond = bas_expr(&q, end);
    truth = val_truth(&cond);
    val_release(&cond);
    if (truth) {
        *p = q;
        return;
    }
    /* False: find the matching WEND first, then carry on just after it.  The
     * scan may walk into later lines, in which case the driver makes the jump. */
    {
        int      depth = 1;
        int      found = 0;
        BasLine *scan = bas_current;
        const char *sp = q;
        const char *se = end;

        while (scan && !found) {
            while (sp < se) {
                if (bas_keyword_at(sp, se, "WHILE")) {
                    depth++;
                    sp += 5;
                    continue;
                }
                if (bas_keyword_at(sp, se, "WEND")) {
                    depth--;
                    sp += 4;
                    if (depth == 0) {
                        found = 1;
                        break;
                    }
                    continue;
                }
                sp++;
            }
            if (!found) {
                scan = scan->next;
                if (scan) {
                    sp = scan->text;
                    se = scan->text + scan->len;
                }
            }
        }
        if (!found)
            bas_raise(E_WHILE_WITHOUT_WEND);
        if (while_depth > 0)
            while_depth--;
        if (scan == bas_current) {
            *p = sp;
            return;
        }
        bas_jump_line_offset((int)scan->number, (int)(sp - scan->text));
        *p = q;
    }
}

static void do_wend(const char **p, const char *end)
{
    WhileFrame *w;

    (void)end;
    if (while_depth == 0)
        bas_raise(E_WEND_WITHOUT_WHILE);
    w = &while_stack[while_depth - 1];
    *p = *p;
    bas_jump_line_offset(w->line, w->offset);
}

/* --------------------------------------------------------------- statements */

/* GOTO n */
static void do_goto(const char **p, const char *end)
{
    const char *q = *p;
    BasValue    v;

    bas_skip_spaces(&q, end);
    v = bas_expr(&q, end);
    bas_jump_line(val_int_of(&v));
    val_release(&v);
    *p = q;
}

/* GOSUB n */
static void do_gosub(const char **p, const char *end)
{
    const char *q = *p;
    BasValue    v;
    int         number;

    bas_skip_spaces(&q, end);
    v = bas_expr(&q, end);
    number = val_int_of(&v);
    val_release(&v);
    bas_gosub_push(bas_current_line, (int)(q - bas_line_text));
    bas_jump_line(number);
    *p = q;
}

/* RETURN [n] */
static void do_return(const char **p, const char *end)
{
    const char *q = *p;

    bas_skip_spaces(&q, end);
    if (q < end && *q != ':') {
        /* RETURN with a line number: GW-BASIC searches the GOSUB stack for it,
         * which also releases the frames above it. */
        BasValue v = bas_expr(&q, end);
        int      number = val_int_of(&v);
        int      i;

        val_release(&v);
        for (i = gosub_depth - 1; i >= 0; i--) {
            if (gosub_stack[i].line == number) {
                gosub_depth = i;
                break;
            }
        }
        if (i < 0)
            bas_raise(E_UNDEFINED_LINE);
    }
    if (gosub_depth <= 0)
        bas_raise(E_RETURN_WITHOUT_GOSUB);
    gosub_depth--;
    bas_jump_line_offset(gosub_stack[gosub_depth].line,
                         gosub_stack[gosub_depth].offset);
    *p = q;
}

/* LET var = expr  (the LET keyword is optional) */
static void do_let(const char **p, const char *end)
{
    const char *q = *p;
    BasValue   *slot;
    BasValue    value;
    int         is_slice = 0, start = 0, len = 0;
    BasValue    slice_val;

    slice_val.type = 0;
    bas_skip_spaces(&q, end);            /* "LET A=1" and "A=1" both work */
    slot = lvalue(&q, end, &is_slice, &slice_val, &start, &len);
    bas_skip_spaces(&q, end);
    if (q >= end || *q != '=')
        bas_raise(E_SYNTAX);
    q++;
    value = bas_expr(&q, end);
    if (slot) {
        if (is_slice)
            assign_slice(slot, value, start, len);
        else
            assign_to(slot, value);
    } else {
        val_release(&value);        /* INKEY$ = ... discards the value */
    }
    if (slice_val.type)
        val_release(&slice_val);
    *p = q;
}

/* READ var{,var} */
static void do_read(const char **p, const char *end)
{
    const char *q = *p;

    bas_skip_spaces(&q, end);
    for (;;) {
        BasValue   *slot;
        BasValue    value;
        int         is_slice = 0, start = 0, len = 0;
        BasValue    slice_val;

        slice_val.type = 0;
        slot = lvalue(&q, end, &is_slice, &slice_val, &start, &len);
        value = data_item();
        if (slot) {
            if (is_slice)
                assign_slice(slot, value, start, len);
            else
                assign_to(slot, value);
        } else {
            val_release(&value);
        }
        if (slice_val.type)
            val_release(&slice_val);
        bas_skip_spaces(&q, end);
        if (q >= end || *q == ':')
            break;
        if (q < end && *q == ',') {
            q++;
            continue;
        }
        break;
    }
    *p = q;
}

/* DATA items are collected by data_scan() before a run starts; executing the
 * statement itself only has to step over its own text, which ends at the next
 * ':' rather than at the end of the line. */
static void do_data(const char **p, const char *end)
{
    const char *q = *p;
    int         in_quotes = 0;

    while (q < end) {
        if (*q == '"')
            in_quotes = !in_quotes;
        else if (*q == ':' && !in_quotes)
            break;
        q++;
    }
    *p = q;
}

/* RESTORE [n] */
static void do_restore(const char **p, const char *end)
{
    const char *q = *p;

    bas_skip_spaces(&q, end);
    if (q < end && *q != ':') {
        BasValue v = bas_expr(&q, end);
        int      number = val_int_of(&v);
        int      i;

        val_release(&v);
        for (i = 0; i < data_count; i++) {
            if ((int)data_refs[i].line->number == number)
                break;
        }
        if (i >= data_count)
            bas_raise(E_UNDEFINED_LINE);
        data_index = i;
        data_offset = 0;
    } else {
        data_index = 0;
        data_offset = 0;
    }
    *p = q;
}

/* RANDOMIZE [expr] */
static void do_randomize(const char **p, const char *end)
{
    const char *q = *p;
    int         have = 0, seed = 0;

    bas_skip_spaces(&q, end);
    if (q < end && *q != ':') {
        BasValue v = bas_expr(&q, end);

        seed = val_int_of(&v);
        val_release(&v);
        have = 1;
    }
    bas_randomize(seed, have);
    *p = q;
}

/* OPTION BASE n */
static void do_option(const char **p, const char *end)
{
    const char *q = *p;
    BasValue    v;

    bas_skip_spaces(&q, end);
    if (!bas_keyword(&q, end, "BASE"))
        bas_raise(E_SYNTAX);
    bas_skip_spaces(&q, end);
    v = bas_expr(&q, end);
    if (v.type != T_INT)
        val_promote(&v, T_INT);
    if (val_int_of(&v) != 0 && val_int_of(&v) != 1)
        bas_raise(E_ILLEGAL_FUNCTION_CALL);
    bas_option_base = (uint8_t)val_int_of(&v);
    val_release(&v);
    *p = q;
}

/* DEF FN name(params) = body.  The dispatcher has already consumed "DEF", so
 * the text starts with the function name (GW-BASIC writes "DEF FNsq(x)=..."). */
static void do_def_fn(const char **p, const char *end)
{
    const char *q = *p;

    bas_skip_spaces(&q, end);
    if (q + 2 <= end && bas_upcase((uint8_t)q[0]) == 'F' &&
        bas_upcase((uint8_t)q[1]) == 'N')
        q += 2;                     /* an optional FN prefix before the name */
    bas_def_fn(&q, end);
    *p = q;
}

/* ON ERROR GOTO/RESUME, ON expr GOTO/GOSUB list */
static void do_on(const char **p, const char *end)
{
    const char *q = *p;
    int         count = 0, chosen = 0, target = 0, is_gosub = 0;
    BasValue    v;

    bas_skip_spaces(&q, end);
    if (bas_keyword(&q, end, "ERROR")) {
        bas_skip_spaces(&q, end);
        if (bas_keyword(&q, end, "GOTO")) {
            err_handler_active = 1;
            err_handler_line = 0;
            if (q < end) {
                v = bas_expr(&q, end);
                err_handler_line = val_int_of(&v);
                val_release(&v);
            }
        } else if (bas_keyword(&q, end, "RESUME")) {
            err_handler_active = 1;
            err_handler_line = 0;
        } else {
            bas_raise(E_SYNTAX);
        }
        *p = q;
        return;
    }
    /* ON <expression> GOTO|GOSUB <line>[,<line>...] */
    v = bas_expr(&q, end);
    if (v.type != T_INT)
        val_promote(&v, T_INT);
    count = val_int_of(&v);
    val_release(&v);
    if (count < 0 || count > 255)
        bas_raise(E_ILLEGAL_FUNCTION_CALL);
    bas_skip_spaces(&q, end);
    if (bas_keyword(&q, end, "GOSUB"))
        is_gosub = 1;
    else if (bas_keyword(&q, end, "GOTO"))
        is_gosub = 0;
    else
        bas_raise(E_SYNTAX);
    for (;;) {
        int number;

        bas_skip_spaces(&q, end);
        if (q >= end || *q == ':')
            break;
        v = bas_expr(&q, end);
        number = val_int_of(&v);
        val_release(&v);
        if (count == chosen + 1)
            target = number;
        chosen++;
        bas_skip_spaces(&q, end);
        if (q < end && *q == ',') {
            q++;
            continue;
        }
        break;
    }
    if (count >= 1 && count <= chosen) {
        if (is_gosub)
            bas_gosub_push(bas_current_line, (int)(q - bas_line_text));
        bas_jump_line(target);
    }
    *p = q;
}

/* RESUME [n|0|NEXT] */
static void do_resume(const char **p, const char *end)
{
    const char *q = *p;

    if (!err_handler_active)
        bas_raise(E_NO_RESUME);
    bas_skip_spaces(&q, end);
    if (q >= end || *q == ':') {
        if (!resume_valid)
            bas_raise(E_RESUME_WITHOUT_ERROR);
        bas_jump_line_offset(resume_line, resume_offset);
    } else if (bas_keyword(&q, end, "NEXT")) {
        if (!resume_valid)
            bas_raise(E_RESUME_WITHOUT_ERROR);
        bas_jump_line(resume_line);
    } else {
        BasValue v = bas_expr(&q, end);
        int      number = val_int_of(&v);

        val_release(&v);
        if (number == 0) {
            stacks_clear();
            data_index = 0;
            data_offset = 0;
        } else {
            bas_jump_line(number);
        }
    }
    err_handler_active = 0;
    *p = q;
}

/* STOP / END: leave the driver loop.  The line list is walked in order, so a
 * jump to a line number above every stored line ends the run cleanly. */
static void do_stop(const char **p, const char *end)
{
    (void)p;
    (void)end;
    if (bas_current) {
        cont_line = bas_current_line;
        cont_offset = exec_offset;
        bas_cont_valid = 1;
    }
    stacks_clear();
    bas_jump_line(65535);
}

/* SWAP a, b */
static void do_swap(const char **p, const char *end)
{
    const char *q = *p;
    BasValue   *a, *b;
    int         sa = 0, sb = 0, st = 0;
    BasValue    va, vb, tmp;

    va.type = vb.type = tmp.type = 0;
    bas_skip_spaces(&q, end);
    a = lvalue(&q, end, &sa, &va, &st, &st);
    if (!a || sa)
        bas_raise(E_ILLEGAL_FUNCTION_CALL);
    bas_skip_spaces(&q, end);
    if (q >= end || *q != ',')
        bas_raise(E_SYNTAX);
    q++;
    b = lvalue(&q, end, &sb, &vb, &st, &st);
    if (!b || sb)
        bas_raise(E_ILLEGAL_FUNCTION_CALL);
    if (a->type != b->type)
        bas_raise(E_TYPE_MISMATCH);
    tmp = *a;
    *a = *b;
    *b = tmp;
    *p = q;
}

/* ------------------------------------------------------------- graphics I/O */

static int color_arg(const char **p, const char *end)
{
    const char *q = *p;
    BasValue    v;
    int         c;

    bas_skip_spaces(&q, end);
    if (q >= end || *q == ')' || *q == ',' || *q == ':') {
        *p = q;
        return -1;
    }
    v = bas_expr(&q, end);
    c = val_int_of(&v);
    val_release(&v);
    *p = q;
    return c;
}

/* PSET (x, y)[, color[, PSET|PRESET]] */
static void do_pset(const char **p, const char *end)
{
    const char *q = *p;
    BasValue    x, y;
    int         color = -1, preset = 0;

    bas_skip_spaces(&q, end);
    if (q >= end || *q != '(')
        bas_raise(E_SYNTAX);
    q++;
    x = bas_expr(&q, end);
    bas_skip_spaces(&q, end);
    if (q >= end || *q != ',')
        bas_raise(E_SYNTAX);
    q++;
    y = bas_expr(&q, end);
    bas_skip_spaces(&q, end);
    if (q >= end || *q != ')')
        bas_raise(E_SYNTAX);
    q++;
    if (q < end && *q == ',') {
        q++;
        color = color_arg(&q, end);
        bas_skip_spaces(&q, end);
        if (q < end && *q == ',') {
            q++;
            if (bas_keyword(&q, end, "PRESET"))
                preset = 1;
            else if (!bas_keyword(&q, end, "PSET"))
                bas_raise(E_SYNTAX);
        }
    }
    bas_pset(val_dbl_of(&x), val_dbl_of(&y), color, preset);
    val_release(&x);
    val_release(&y);
    *p = q;
}

/* LINE [(x1,y1)]-(x2,y2)[,color][[B][F]][,style] */
static void do_line_stmt(const char **p, const char *end)
{
    const char *q = *p;
    BasValue    x1, y1, x2, y2;
    int         color = -1, box = 0, fill = 0, style = 0;
    int         have_start = 0;

    x1.type = y1.type = 0;
    bas_skip_spaces(&q, end);
    if (q < end && *q == '(') {
        q++;
        x1 = bas_expr(&q, end);
        bas_skip_spaces(&q, end);
        if (q >= end || *q != ',')
            bas_raise(E_SYNTAX);
        q++;
        y1 = bas_expr(&q, end);
        bas_skip_spaces(&q, end);
        if (q >= end || *q != ')')
            bas_raise(E_SYNTAX);
        q++;
        have_start = 1;
    }
    bas_skip_spaces(&q, end);
    if (q >= end || *q != '-')
        bas_raise(E_SYNTAX);
    q++;
    if (q < end && *q == '(') {
        q++;
        x2 = bas_expr(&q, end);
        bas_skip_spaces(&q, end);
        if (q >= end || *q != ',')
            bas_raise(E_SYNTAX);
        q++;
        y2 = bas_expr(&q, end);
        bas_skip_spaces(&q, end);
        if (q >= end || *q != ')')
            bas_raise(E_SYNTAX);
        q++;
    } else {
        bas_raise(E_SYNTAX);
    }
    if (q < end && *q == ',') {
        q++;
        color = color_arg(&q, end);
    }
    bas_skip_spaces(&q, end);
    if (q < end && *q == '[') {
        q++;
        if (q < end && (*q == 'B' || *q == 'b')) {
            box = 1;
            q++;
            if (q < end && (*q == 'F' || *q == 'f')) {
                fill = 1;
                q++;
            }
        } else if (q < end && (*q == 'F' || *q == 'f')) {
            box = 1;
            fill = 1;
            q++;
        }
        if (q >= end || *q != ']')
            bas_raise(E_SYNTAX);
        q++;
    }
    bas_skip_spaces(&q, end);
    if (q < end && *q == ',') {
        q++;
        style = color_arg(&q, end);
    }
    if (!have_start) {
        /* LINE -(x,y) continues from the last endpoint. */
        x1.type = T_INT;
        x1.v.i = bas_last_x;
        y1.type = T_INT;
        y1.v.i = bas_last_y;
    }
    bas_line_draw(val_dbl_of(&x1), val_dbl_of(&y1), val_dbl_of(&x2), val_dbl_of(&y2), color,
                  style, box, fill);
    val_release(&x1);
    val_release(&y1);
    val_release(&x2);
    val_release(&y2);
    *p = q;
}

/* CIRCLE (x, y), r[, color[, start, end[, aspect]]] */
static void do_circle(const char **p, const char *end)
{
    const char *q = *p;
    BasValue    args[7];
    int         nargs = 0;

    bas_skip_spaces(&q, end);
    if (q >= end || *q != '(')
        bas_raise(E_SYNTAX);
    q++;
    while (nargs < 2) {
        args[nargs++] = bas_expr(&q, end);
        bas_skip_spaces(&q, end);
        if (q >= end || *q != ',')
            bas_raise(E_SYNTAX);
        q++;
    }
    args[nargs++] = bas_expr(&q, end);
    bas_skip_spaces(&q, end);
    if (q < end && *q == ')') {
        q++;
    } else if (q < end && *q == ',') {
        q++;
        /* The third value is the colour; start/end/aspect may follow. */
        while (nargs < 6) {
            args[nargs] = bas_expr(&q, end);
            nargs++;
            bas_skip_spaces(&q, end);
            if (q < end && *q == ',') {
                q++;
                bas_skip_spaces(&q, end);
                if (q < end && *q == ')')
                    break;
                continue;
            }
            break;
        }
        if (q >= end || *q != ')')
            bas_raise(E_SYNTAX);
        q++;
    } else {
        bas_raise(E_SYNTAX);
    }
    {
        double x = val_dbl_of(&args[0]);
        double y = val_dbl_of(&args[1]);
        double r = val_dbl_of(&args[2]);
        int    color = -1;
        double start = 0, stop = 6.283185307179586, aspect = 1;

        if (nargs >= 4)
            color = val_int_of(&args[3]);
        if (nargs >= 5)
            start = val_dbl_of(&args[4]);
        if (nargs >= 6)
            stop = val_dbl_of(&args[5]);
        if (nargs >= 7)
            aspect = val_dbl_of(&args[6]);
        bas_circle_draw(x, y, r, color, start, stop, aspect);
    }
    {
        int i;

        for (i = 0; i < nargs; i++)
            val_release(&args[i]);
    }
    *p = q;
}

/* PAINT (x, y)[, fillcolor[, bordercolor]] */
static void do_paint(const char **p, const char *end)
{
    const char *q = *p;
    BasValue    x, y;
    int         color = -1, border = -1;

    bas_skip_spaces(&q, end);
    if (q >= end || *q != '(')
        bas_raise(E_SYNTAX);
    q++;
    x = bas_expr(&q, end);
    bas_skip_spaces(&q, end);
    if (q >= end || *q != ',')
        bas_raise(E_SYNTAX);
    q++;
    y = bas_expr(&q, end);
    bas_skip_spaces(&q, end);
    if (q >= end || *q != ')')
        bas_raise(E_SYNTAX);
    q++;
    if (q < end && *q == ',') {
        q++;
        color = color_arg(&q, end);
        bas_skip_spaces(&q, end);
        if (q < end && *q == ',') {
            q++;
            border = color_arg(&q, end);
        }
    }
    bas_paint_at(val_dbl_of(&x), val_dbl_of(&y), color, border);
    val_release(&x);
    val_release(&y);
    *p = q;
}

/* SCREEN n */
static void do_screen(const char **p, const char *end)
{
    const char *q = *p;
    BasValue    v;

    bas_skip_spaces(&q, end);
    v = bas_expr(&q, end);
    bas_screen(val_int_of(&v));
    val_release(&v);
    *p = q;
}

/* CLS */
static void do_cls(const char **p, const char *end)
{
    (void)p;
    (void)end;
    bas_cls();
}

/* COLOR [fg[, bg]] */
static void do_color(const char **p, const char *end)
{
    const char *q = *p;
    int         fg = -1, bg = -1;

    bas_skip_spaces(&q, end);
    if (q < end && *q != ':') {
        fg = color_arg(&q, end);
        bas_skip_spaces(&q, end);
        if (q < end && *q == ',') {
            q++;
            bg = color_arg(&q, end);
        }
    }
    bas_color(fg, bg);
    *p = q;
}

/* LOCATE row[, col] */
static void do_locate(const char **p, const char *end)
{
    const char *q = *p;
    BasValue    row, col;

    row.type = col.type = 0;
    bas_skip_spaces(&q, end);
    row = bas_expr(&q, end);
    bas_skip_spaces(&q, end);
    if (q < end && *q == ',') {
        q++;
        col = bas_expr(&q, end);
    }
    gfx_locate(val_int_of(&row), (col.type ? val_int_of(&col) : -1));
    if (row.type)
        val_release(&row);
    if (col.type)
        val_release(&col);
    *p = q;
}

/* ------------------------------------------------------------- hardware I/O */

/* OUT port, data */
static void do_out(const char **p, const char *end)
{
    const char *q = *p;
    BasValue    port, data;

    bas_skip_spaces(&q, end);
    port = bas_expr(&q, end);
    bas_skip_spaces(&q, end);
    if (q >= end || *q != ',')
        bas_raise(E_SYNTAX);
    q++;
    data = bas_expr(&q, end);
    bas_plat_out(val_int_of(&port), val_int_of(&data));
    val_release(&port);
    val_release(&data);
    *p = q;
}

/* WAIT port, mask, pattern */
static void do_wait(const char **p, const char *end)
{
    const char *q = *p;
    BasValue    port, mask, pattern;
    int         i;

    bas_skip_spaces(&q, end);
    port = bas_expr(&q, end);
    mask.type = pattern.type = 0;
    for (i = 0; i < 2; i++) {
        bas_skip_spaces(&q, end);
        if (q >= end || *q != ',')
            bas_raise(E_SYNTAX);
        q++;
        if (i == 0)
            mask = bas_expr(&q, end);
        else
            pattern = bas_expr(&q, end);
    }
    for (;;) {
        if ((bas_plat_in(val_int_of(&port)) & val_int_of(&mask)) == val_int_of(&pattern))
            break;
        if (con_break_pending())
            bas_raise(E_BREAK);
    }
    val_release(&port);
    val_release(&mask);
    val_release(&pattern);
    *p = q;
}

/* BIOS n, a0, a1, a2, a3, a4, a5  — the raw ecall service. */
static void do_bios(const char **p, const char *end)
{
    const char *q = *p;
    BasValue    a[7];
    int         nargs = 0, i;

    bas_skip_spaces(&q, end);
    while (nargs < 7) {
        a[nargs++] = bas_expr(&q, end);
        bas_skip_spaces(&q, end);
        if (q < end && *q == ',') {
            q++;
            continue;
        }
        break;
    }
    {
        uint32_t args[6];

        for (i = 0; i < 6; i++)
            args[i] = (i + 1 < nargs) ? (uint32_t)val_int_of(&a[i + 1]) : 0;
        bas_bios_call(val_int_of(&a[0]), args);
    }
    for (i = 0; i < nargs; i++)
        val_release(&a[i]);
    *p = q;
}

/* ------------------------------------------------------- direct-mode commands */

static void do_system(const char **p, const char *end)
{
    (void)p;
    (void)end;
    con_flush();
    bas_plat_exit();
}

static void do_tests(const char **p, const char *end)
{
    (void)p;
    (void)end;
    bas_selftest();
}

static void do_tron(const char **p, const char *end)
{
    (void)p;
    (void)end;
    bas_trace = 1;
}

static void do_troff(const char **p, const char *end)
{
    (void)p;
    (void)end;
    bas_trace = 0;
}

static void do_cont(const char **p, const char *end)
{
    (void)p;
    (void)end;
    if (!bas_cont_valid)
        bas_raise(E_CANT_CONTINUE);
    bas_run_program(cont_line, 1);
}

static void do_run(const char **p, const char *end)
{
    const char *q = *p;
    int         start = 0;

    bas_skip_spaces(&q, end);
    if (q < end && *q != ':') {
        BasValue v = bas_expr(&q, end);

        start = val_int_of(&v);
        val_release(&v);
    }
    /* RUN clears the variables, exactly as GW-BASIC does. */
    bas_for_reset();
    bas_vars_clear();
    bas_data_reset();
    bas_run_program(start, 0);
    *p = q;
}

static void do_new(const char **p, const char *end)
{
    (void)p;
    (void)end;
    bas_new();
}

static void do_list(const char **p, const char *end)
{
    const char *q = *p;
    int         from = 0, to = 65535;

    bas_skip_spaces(&q, end);
    if (q < end && *q != ':') {
        BasValue v = bas_expr(&q, end);

        from = val_int_of(&v);
        val_release(&v);
        bas_skip_spaces(&q, end);
        if (q < end && *q == '-') {
            q++;
            bas_skip_spaces(&q, end);
            if (q < end && *q != ':') {
                v = bas_expr(&q, end);
                to = val_int_of(&v);
                val_release(&v);
            }
        }
    }
    bas_list(from, to, 1);
    *p = q;
}

static void do_renum(const char **p, const char *end)
{
    const char *q = *p;
    int         first = 10, step = 10, seen = 0;

    bas_skip_spaces(&q, end);
    while (q < end && *q != ':') {
        BasValue v = bas_expr(&q, end);
        int      n = val_int_of(&v);

        val_release(&v);
        if (!seen)
            first = n;
        else
            step = n;
        seen++;
        bas_skip_spaces(&q, end);
        if (q < end && *q == ',') {
            q++;
            continue;
        }
        break;
    }
    bas_renum(first, step);
    *p = q;
}

static void do_delete(const char **p, const char *end)
{
    const char *q = *p;
    int         from = 0, to = 65535;

    bas_skip_spaces(&q, end);
    if (q < end && *q != ':') {
        BasValue v = bas_expr(&q, end);

        from = val_int_of(&v);
        val_release(&v);
        bas_skip_spaces(&q, end);
        if (q < end && *q == '-') {
            q++;
            bas_skip_spaces(&q, end);
            if (q < end && *q != ':') {
                v = bas_expr(&q, end);
                to = val_int_of(&v);
                val_release(&v);
            }
        }
    }
    bas_delete(from, to);
    *p = q;
}

static void do_save(const char **p, const char *end)
{
    (void)p;
    (void)end;
    bas_save_unavailable();
}

static void do_load(const char **p, const char *end)
{
    const char *q = *p;
    BasValue    name;

    bas_skip_spaces(&q, end);
    name = bas_expr(&q, end);
    if (name.type != T_STR)
        bas_raise(E_TYPE_MISMATCH);
    bas_load(str_ptr(name.v.s));
    val_release(&name);
    *p = q;
}

static void do_clear(const char **p, const char *end)
{
    (void)p;
    (void)end;
    bas_vars_clear();
    stacks_clear();
    data_index = 0;
    data_offset = 0;
}

/* ------------------------------------------------------------- the dispatcher */

void bas_exec_statement(const char **p, const char *end)
{
    const char *q = *p;

    bas_skip_spaces(&q, end);
    if (q >= end) {
        *p = q;
        return;
    }
    /* Remember where this statement started: RESUME NEXT and CONT need it. */
    exec_offset = (int)(q - bas_line_text);
    if (!resume_valid && bas_running) {
        resume_line = bas_current_line;
        resume_offset = exec_offset;
        resume_valid = 1;
    }

    if (bas_keyword(&q, end, "PRINT") || bas_keyword(&q, end, "?"))
        do_print(&q, end);
    else if (bas_keyword(&q, end, "INPUT"))
        do_input(&q, end);
    else if (bas_keyword(&q, end, "LINE")) {
        const char *save = q;

        if (bas_keyword(&q, end, "INPUT"))
            do_line_input(&q, end);
        else {
            q = save;
            do_line_stmt(&q, end);
        }
    }
    else if (bas_keyword(&q, end, "IF"))
        do_if(&q, end);
    else if (bas_keyword(&q, end, "FOR"))
        do_for(&q, end);
    else if (bas_keyword(&q, end, "NEXT"))
        do_next(&q, end);
    else if (bas_keyword(&q, end, "WHILE"))
        do_while(&q, end);
    else if (bas_keyword(&q, end, "WEND"))
        do_wend(&q, end);
    else if (bas_keyword(&q, end, "GOTO"))
        do_goto(&q, end);
    else if (bas_keyword(&q, end, "GOSUB"))
        do_gosub(&q, end);
    else if (bas_keyword(&q, end, "RETURN"))
        do_return(&q, end);
    else if (bas_keyword(&q, end, "ON"))
        do_on(&q, end);
    else if (bas_keyword(&q, end, "RESUME"))
        do_resume(&q, end);
    else if (bas_keyword(&q, end, "READ"))
        do_read(&q, end);
    else if (bas_keyword(&q, end, "DATA"))
        do_data(&q, end);
    else if (bas_keyword(&q, end, "RESTORE"))
        do_restore(&q, end);
    else if (bas_keyword(&q, end, "DIM"))
        bas_dim_statement(&q, end);
    else if (bas_keyword(&q, end, "RANDOMIZE"))
        do_randomize(&q, end);
    else if (bas_keyword(&q, end, "OPTION"))
        do_option(&q, end);
    else if (bas_keyword(&q, end, "DEFINT"))
        bas_def_type_range(&q, end, T_INT);
    else if (bas_keyword(&q, end, "DEFSNG"))
        bas_def_type_range(&q, end, T_SNG);
    else if (bas_keyword(&q, end, "DEFDBL"))
        bas_def_type_range(&q, end, T_DBL);
    else if (bas_keyword(&q, end, "DEFSTR"))
        bas_def_type_range(&q, end, T_STR);
    else if (bas_keyword(&q, end, "DEF"))
        do_def_fn(&q, end);
    else if (bas_keyword(&q, end, "LET"))
        do_let(&q, end);
    else if (bas_keyword(&q, end, "REM"))
        q = end;
    else if (bas_keyword(&q, end, "STOP") || bas_keyword(&q, end, "END"))
        do_stop(&q, end);
    else if (bas_keyword(&q, end, "SWAP"))
        do_swap(&q, end);
    else if (bas_keyword(&q, end, "PSET"))
        do_pset(&q, end);
    else if (bas_keyword(&q, end, "CIRCLE"))
        do_circle(&q, end);
    else if (bas_keyword(&q, end, "PAINT"))
        do_paint(&q, end);
    else if (bas_keyword(&q, end, "SCREEN"))
        do_screen(&q, end);
    else if (bas_keyword(&q, end, "CLS"))
        do_cls(&q, end);
    else if (bas_keyword(&q, end, "COLOR"))
        do_color(&q, end);
    else if (bas_keyword(&q, end, "LOCATE"))
        do_locate(&q, end);
    else if (bas_keyword(&q, end, "OUT"))
        do_out(&q, end);
    else if (bas_keyword(&q, end, "WAIT"))
        do_wait(&q, end);
    else if (bas_keyword(&q, end, "BIOS"))
        do_bios(&q, end);
    else if (bas_keyword(&q, end, "SYSTEM"))
        do_system(&q, end);
    else if (bas_keyword(&q, end, "TESTS"))
        do_tests(&q, end);
    else if (bas_keyword(&q, end, "TRON"))
        do_tron(&q, end);
    else if (bas_keyword(&q, end, "TROFF"))
        do_troff(&q, end);
    else if (bas_keyword(&q, end, "CONT"))
        do_cont(&q, end);
    else if (bas_keyword(&q, end, "RUN"))
        do_run(&q, end);
    else if (bas_keyword(&q, end, "NEW"))
        do_new(&q, end);
    else if (bas_keyword(&q, end, "LIST"))
        do_list(&q, end);
    else if (bas_keyword(&q, end, "RENUM"))
        do_renum(&q, end);
    else if (bas_keyword(&q, end, "DELETE"))
        do_delete(&q, end);
    else if (bas_keyword(&q, end, "SAVE"))
        do_save(&q, end);
    else if (bas_keyword(&q, end, "LOAD"))
        do_load(&q, end);
    else if (bas_keyword(&q, end, "CLEAR"))
        do_clear(&q, end);
    else if (bas_keyword(&q, end, "KEY") || bas_keyword(&q, end, "AUTO") ||
             bas_keyword(&q, end, "MERGE") || bas_keyword(&q, end, "CHAIN") ||
             bas_keyword(&q, end, "FILES") || bas_keyword(&q, end, "KILL") ||
             bas_keyword(&q, end, "NAME") || bas_keyword(&q, end, "OPEN") ||
             bas_keyword(&q, end, "CLOSE") || bas_keyword(&q, end, "GET") ||
             bas_keyword(&q, end, "PUT") || bas_keyword(&q, end, "LLIST") ||
             bas_keyword(&q, end, "LPRINT"))
        q = end;
    else {
        /* Not a keyword: an assignment without LET. */
        do_let(&q, end);
    }
    *p = q;
}

/* ---------------------------------------------------------------- driver */

/* A statement asked control to move.  The driver resolves it against the line
 * list; JUMP_RESUME returns to a saved (line, offset) pair. */
/* the jump kinds live in basic.h */
int bas_jump_pending;
int bas_jump_kind;
int bas_jump_target;                /* line number for JUMP_LINE */
int bas_jump_resume_line;           /* line number for JUMP_RESUME */
int bas_jump_offset;                /* offset for JUMP_RESUME */

void bas_jump_line(int number)
{
    bas_jump_pending = 1;
    bas_jump_kind = JUMP_LINE;
    bas_jump_target = number;
}

void bas_jump_line_offset(int number, int offset)
{
    bas_jump_pending = 1;
    bas_jump_kind = JUMP_RESUME;
    bas_jump_resume_line = number;
    bas_jump_offset = offset;
}

void bas_jump_line_to(BasLine *line, int offset)
{
    bas_jump_line_offset((int)line->number, offset);
}

static void trace_line(int number)
{
    char buf[16];
    int  n = 0;

    buf[n++] = '[';
    {
        char tmp[8];
        int  m = 0;
        int  v = number;

        if (v == 0)
            tmp[m++] = '0';
        while (v) {
            tmp[m++] = (char)('0' + v % 10);
            v /= 10;
        }
        while (m)
            buf[n++] = tmp[--m];
    }
    buf[n++] = ']';
    buf[n] = 0;
    con_puts(buf);
    con_newline();
    con_flush();
}

/* Run one line, or the tail of one line, and keep going until the program
 * stops.  A statement that moves control sets bas_jump_pending; the loop below
 * resolves it and resumes at the right line and character offset, which is how
 * NEXT, WEND and RETURN get back into the middle of a line. */
static void run_lines(BasLine *l, int offset)
{
    const char *p = 0;

    for (;;) {
        const char *end;

        if (!p) {
            if (!l)
                break;
            p = l->text + offset;
            offset = 0;
        }
        if (bas_break) {
            cont_line = (int)l->number;
            cont_offset = (int)(p - l->text);
            bas_cont_valid = 1;
            bas_raise_line(E_BREAK, (int)l->number);
        }
        if (bas_trace)
            trace_line((int)l->number);
        bas_current = l;
        exec_offset = (int)(p - l->text);
        end = l->text + l->len;
        bas_jump_pending = 0;
        bas_run_sequence(&p, end, 0);
        if (!bas_jump_pending) {
            l = l->next;
            p = 0;
            continue;
        }
        if (bas_jump_kind == JUMP_LINE) {
            if ((uint32_t)bas_jump_target > BAS_MAX_LINE)
                break;                       /* STOP or END */
            l = find_line_or_die(bas_jump_target);
            p = 0;
        } else {
            l = bas_line_find(bas_jump_resume_line);
            if (!l)
                bas_raise_line(E_UNDEFINED_LINE, bas_jump_resume_line);
            p = l->text + bas_jump_offset;
            if (bas_jump_offset < 0 || (uint32_t)bas_jump_offset > l->len)
                p = 0;
        }
        bas_jump_pending = 0;
    }
}

void bas_run_program(int start_line, int from_continue)
{
    BasLine *l;

    if (start_line > 0) {
        l = find_line_or_die(start_line);
    } else if (from_continue) {
        l = bas_line_find(cont_line);
        if (!l)
            bas_raise(E_CANT_CONTINUE);
    } else {
        l = bas_program;
        if (!l) {
            con_puts("No program\r\n");
            return;
        }
    }
    stacks_clear();
    data_scan();
    bas_running = 1;
    bas_break = 0;
    run_lines(l, from_continue ? cont_offset : 0);
    bas_running = 0;
    bas_cont_valid = 0;
}

void bas_execute(const char *text, int len, int line_number, int direct)
{
    const char *p = text;
    const char *end = text + len;

    (void)line_number;
    (void)direct;
    bas_jump_pending = 0;
    bas_run_sequence(&p, end, 0);
    if (!bas_jump_pending)
        return;
    if (bas_jump_kind != JUMP_LINE)
        bas_raise(E_ILLEGAL_DIRECT);
    /* A jump typed at the prompt starts the program there. */
    bas_running = 1;
    stacks_clear();
    data_scan();
    bas_break = 0;
    if ((uint32_t)bas_jump_target > BAS_MAX_LINE) {
        bas_running = 0;
        return;
    }
    run_lines(find_line_or_die(bas_jump_target), 0);
    bas_running = 0;
    bas_cont_valid = 0;
}