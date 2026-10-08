/* expr.c - expression evaluation for the payload-side GW-BASIC interpreter.
 *
 * The parser works directly on the stored text of a line; there is no token
 * stream.  Values are reference counted: every function that returns a
 * BasValue of type T_STR returns a new reference, and every temporary is
 * released as soon as it is consumed. */
#include "basic.h"
#include <string.h>

/* ------------------------------------------------------------ user functions */

typedef struct UserFn {
    char      name[4];              /* two significant characters + type */
    char      params[8][4];
    int       nparams;
    BasLine  *body;                 /* DEF FN lives on one line */
    int       body_start;           /* offset of the expression in body->text */
    int       type;
} UserFn;

#define MAX_USERFN 32
static UserFn userfn[MAX_USERFN];
static int    userfn_count;

static void fn_key(const char *name, char key[4])
{
    int up0 = bas_upcase((uint8_t)name[0]);
    int up1 = name[1] ? bas_upcase((uint8_t)name[1]) : 0;
    int t   = bas_name_type(name, (int)strlen(name));

    if (up1 == '$' || up1 == '%' || up1 == '!' || up1 == '#')
        up1 = 0;
    key[0] = (char)up0;
    key[1] = (char)up1;
    key[2] = (char)t;
    key[3] = 0;
}

void bas_userfn_clear(void)
{
    userfn_count = 0;
}

static UserFn *userfn_find(const char *name)
{
    char key[4];
    int  i;

    fn_key(name, key);
    for (i = 0; i < userfn_count; i++)
        if (memcmp(userfn[i].name, key, 3) == 0)
            return &userfn[i];
    /* A call may carry the FN prefix the definition does not, and vice versa:
     * DEF FNsq(x) is called as FNsq(x). */
    if (bas_upcase((uint8_t)name[0]) == 'F' && bas_upcase((uint8_t)name[1]) == 'N' &&
        name[2]) {
        fn_key(name + 2, key);
        for (i = 0; i < userfn_count; i++)
            if (memcmp(userfn[i].name, key, 3) == 0)
                return &userfn[i];
    }
    return 0;
}

/* DEF FN name(params) = expression : the whole definition must fit on one
 * line, exactly as GW-BASIC requires. */
void bas_def_fn(const char **p, const char *end)
{
    char      name[16];
    UserFn   *fn;
    const char *q = *p;

    bas_skip_spaces(&q, end);
    if (!bas_read_name(&q, end, name))
        bas_raise(E_SYNTAX);
    if (userfn_count >= MAX_USERFN)
        bas_raise(E_OUT_OF_MEMORY);
    fn = userfn_find(name);
    if (!fn) {
        fn = &userfn[userfn_count++];
        fn_key(name, fn->name);
        fn->nparams = 0;
        fn->body = bas_current;
        fn->type = bas_name_type(name, (int)strlen(name));
    }
    bas_skip_spaces(&q, end);
    if (q < end && *q == '(') {
        q++;
        for (;;) {
            char pname[16];

            bas_skip_spaces(&q, end);
            if (!bas_read_name(&q, end, pname))
                bas_raise(E_SYNTAX);
            if (fn->nparams >= 8)
                bas_raise(E_ILLEGAL_FUNCTION_CALL);
            fn_key(pname, fn->params[fn->nparams++]);
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
    }
    bas_skip_spaces(&q, end);
    if (q >= end || *q != '=')
        bas_raise(E_SYNTAX);
    q++;
    /* The body is the rest of the statement: it is re-parsed and evaluated on
     * every call, so the definition only stores where it starts. */
    fn->body_start = (int)(q - (const char *)bas_current->text);
    while (q < end && *q != ':')
        q++;
    *p = q;
}

/* ------------------------------------------------------------- array access */

static BasValue *array_element(BasVar *var, const char **p, const char *end)
{
    BasArray *a = var->array;
    int32_t   sub[BAS_MAX_DIMS];
    int       n = 0;
    int32_t   index = 0, stride;
    const char *q = *p;

    if (!a) {
        /* Not dimensioned: GW-BASIC assumes a single dimension of 10. */
        int32_t counts[1];

        counts[0] = 11;
        a = bas_array_dim(var, 1, counts);
    }
    for (;;) {
        BasValue v;

        bas_skip_spaces(&q, end);
        v = bas_expr(&q, end);
        if (v.type != T_INT) {
            int32_t i = val_integer(&v);

            val_release(&v);
            v.type = T_INT;
            v.v.i = i;
        }
        if (n >= BAS_MAX_DIMS)
            bas_raise(E_SUBSCRIPT);
        sub[n++] = v.v.i;
        val_release(&v);
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
    *p = q;
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
    return &a->data[index];
}

/* ------------------------------------------------------------ user function call */

static BasValue call_userfn(UserFn *fn, const char **p, const char *end)
{
    BasValue   result;
    BasVar   **saved = 0;
    BasValue  *oldval = 0;
    int        i, n = fn->nparams;
    const char *q = *p;
    int        used = 0;

    if (q >= end || *q != '(')
        bas_raise(E_SYNTAX);
    q++;
    if (n > 0) {
        saved = (BasVar **)bas_alloc((uint32_t)n * sizeof(BasVar *));
        oldval = (BasValue *)bas_alloc((uint32_t)n * sizeof(BasValue));
        if (!saved || !oldval)
            bas_raise(E_OUT_OF_MEMORY);
        for (i = 0; i < n; i++) {
            BasValue v = bas_expr(&q, end);
            BasVar  *pv;

            pv = bas_var(fn->params[i], 3, 1);
            /* The parameter is a fresh variable of the function's own type. */
            saved[i] = pv;
            oldval[i] = pv->value;
            pv->value.type = (uint8_t)fn->type;
            pv->value.v.d = 0.0;
            if (fn->type == T_STR)
                pv->value.v.s = 0;
            val_promote(&v, fn->type);
            pv->value = v;
            pv->used = 1;
            used++;
            bas_skip_spaces(&q, end);
            if (i + 1 < n) {
                if (q >= end || *q != ',')
                    bas_raise(E_SYNTAX);
                q++;
            }
        }
    }
    if (q >= end || *q != ')')
        bas_raise(E_SYNTAX);
    q++;
    *p = q;
    if (!fn->body)
        bas_raise(E_UNDEFINED_FUNCTION);
    /* Evaluate the body text in place. */
    {
        const char *line_start = fn->body->text;
        const char *line_end = line_start + fn->body->len;
        const char *bp = line_start + fn->body_start;

        result = bas_expr(&bp, line_end);
    }
    for (i = 0; i < used; i++) {
        val_release(&saved[i]->value);
        saved[i]->value = oldval[i];
    }
    if (saved)
        bas_free(saved);
    if (oldval)
        bas_free(oldval);
    return result;
}

/* ------------------------------------------------------------- primaries */

static BasValue primary(const char **p, const char *end)
{
    const char *q = *p;
    BasValue    v;

    bas_skip_spaces(&q, end);
    if (q >= end)
        bas_raise(E_MISSING_OPERAND);
    if (*q == '(') {
        q++;
        v = bas_expr(&q, end);
        bas_skip_spaces(&q, end);
        if (q >= end || *q != ')')
            bas_raise(E_SYNTAX);
        q++;
        *p = q;
        return v;
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
        v.type = T_STR;
        v.v.s = str_from(s, (uint32_t)len);
        q++;
        *p = q;
        return v;
    }
    {
        char name[16];

        if (bas_read_name(&q, end, name)) {
            const BasBuiltin *b = bas_builtin_find(name);
            BasVar           *var;

            if (b) {
                BasValue args[4];
                int      nargs = 0;
                int      i;

                bas_skip_spaces(&q, end);
                if (b->min_args < 0) {
                    /* Parentheses optional: RND, TIMER, DATE$, ... */
                    if (q < end && *q == '(') {
                        q++;
                        bas_skip_spaces(&q, end);
                        if (!(q < end && *q == ')')) {
                            while (nargs < b->max_args) {
                                args[nargs++] = bas_expr(&q, end);
                                bas_skip_spaces(&q, end);
                                if (nargs > 0 && q < end && *q == ',') {
                                    q++;
                                    bas_skip_spaces(&q, end);
                                    continue;
                                }
                                break;
                            }
                        }
                        if (q >= end || *q != ')')
                            bas_raise(E_SYNTAX);
                        q++;
                    }
                } else {
                    if (q >= end || *q != '(')
                        bas_raise(E_SYNTAX);
                    q++;
                    while (nargs < b->max_args) {
                        bas_skip_spaces(&q, end);
                        if (nargs > 0) {
                            if (q < end && *q == ',') {
                                q++;
                                bas_skip_spaces(&q, end);
                            } else {
                                break;
                            }
                        }
                        args[nargs++] = bas_expr(&q, end);
                        bas_skip_spaces(&q, end);
                    }
                    if (q >= end || *q != ')')
                        bas_raise(E_SYNTAX);
                    q++;
                    if (nargs < b->min_args)
                        bas_raise(E_ILLEGAL_FUNCTION_CALL);
                }
                *p = q;
                v = bas_builtin_call(b, name, args, nargs);
                for (i = 0; i < nargs; i++)
                    val_release(&args[i]);
                return v;
            }
            {
                UserFn *fn = userfn_find(name);

                if (fn) {
                    *p = q;
                    return call_userfn(fn, p, end);
                }
            }
            var = bas_var(name, (int)strlen(name), 1);
            bas_skip_spaces(&q, end);
            if (q < end && *q == '(') {
                BasValue *slot;

                q++;
                if (!var->is_array && var->used)
                    bas_raise(E_DUPLICATE_DEF);   /* was a scalar */
                var->used = 1;
                slot = array_element(var, &q, end);
                *p = q;
                if (slot->type != var->type)
                    bas_raise(E_TYPE_MISMATCH);
                v = *slot;
                if (v.type == T_STR)
                    str_retain(v.v.s);
                return v;
            }
            if (var->is_array)
                bas_raise(E_ILLEGAL_FUNCTION_CALL);
            var->used = 1;
            *p = q;
            v = var->value;
            if (v.type == T_STR)
                str_retain(v.v.s);
            return v;
        }
    }
    if (bas_scan_number(&q, end, &v)) {
        *p = q;
        return v;
    }
    bas_raise(E_SYNTAX);
    return val_int(0);
}

/* ------------------------------------------------------------- operators */

static int relop(const char **p, const char *end, int *op)
{
    static const struct { const char *text; int op; } ops[] = {
        { "<>", OP_NE }, { "=", OP_EQ }, { "<", OP_LT }, { ">", OP_GT },
    };
    const char *q = *p;
    unsigned    i;

    for (i = 0; i < sizeof(ops) / sizeof(ops[0]); i++) {
        size_t n = strlen(ops[i].text);

        if ((size_t)(end - q) >= n && memcmp(q, ops[i].text, n) == 0) {
            /* "<" must not swallow "<=" or "<>" twice, and ">=" is two chars. */
            if (ops[i].op == OP_LT && q + 1 < end && (q[1] == '=' || q[1] == '>'))
                continue;
            if (ops[i].op == OP_GT && q + 1 < end && q[1] == '=')
                continue;
            q += n;
            *op = ops[i].op;
            *p = q;
            return 1;
        }
    }
    if (q + 1 < end && ((q[0] == '<' && q[1] == '=') || (q[0] == '>' && q[1] == '='))) {
        *op = (q[0] == '<') ? OP_LE : OP_GE;
        *p = q + 2;
        return 1;
    }
    return 0;
}

static BasValue relational(int op, const BasValue *a, const BasValue *b)
{
    int c, truth;

    c = val_compare(a, b);
    switch (op) {
    case OP_EQ: truth = (c == 0);  break;
    case OP_NE: truth = (c != 0);  break;
    case OP_LT: truth = (c < 0);   break;
    case OP_GT: truth = (c > 0);   break;
    case OP_LE: truth = (c <= 0);  break;
    default:    truth = (c >= 0);  break;
    }
    return val_int(truth ? -1 : 0);
}

static BasValue unary(const char **p, const char *end)
{
    const char *q = *p;
    BasValue    v;

    bas_skip_spaces(&q, end);
    if (q < end && (*q == '-' || *q == '+')) {
        int op = *q;

        q++;
        v = unary(&q, end);
        if (op == '-')
            v = val_negate(&v);
        *p = q;
        return v;
    }
    if (q < end && *q == 'N' && bas_keyword(&q, end, "NOT")) {
        BasValue n;

        n = unary(&q, end);
        v = val_bitnot(&n);
        val_release(&n);
        *p = q;
        return v;
    }
    *p = q;
    return primary(p, end);
}

static BasValue power(const char **p, const char *end)
{
    BasValue a = unary(p, end);
    const char *q = *p;

    bas_skip_spaces(&q, end);
    if (q < end && *q == '^') {
        BasValue b;

        q++;
        *p = q;
        b = power(&q, end);              /* right associative */
        *p = q;
        a = bas_arith(OP_POW, &a, &b);
        *p = q;
        return a;
    }
    *p = q;
    return a;
}

static BasValue mul_level(const char **p, const char *end)
{
    BasValue a = power(p, end);
    const char *q = *p;

    for (;;) {
        int      op;
        BasValue b;

        bas_skip_spaces(&q, end);
        if (q < end && (*q == '*' || *q == '/' || *q == '\\')) {
            op = *q;
            q++;
        } else if (q < end && bas_keyword(&q, end, "MOD")) {
            op = OP_MOD;
        } else {
            break;
        }
        *p = q;
        b = power(&q, end);
        *p = q;
        a = bas_arith(op, &a, &b);
    }
    *p = q;
    return a;
}

static BasValue add_level(const char **p, const char *end)
{
    BasValue a = mul_level(p, end);
    const char *q = *p;

    for (;;) {
        int      op;
        BasValue b;

        bas_skip_spaces(&q, end);
        if (q < end && (*q == '+' || *q == '-')) {
            op = *q;
            q++;
        } else {
            break;
        }
        *p = q;
        b = mul_level(&q, end);
        *p = q;
        a = bas_arith(op, &a, &b);
    }
    *p = q;
    return a;
}

static BasValue relational_level(const char **p, const char *end)
{
    BasValue a = add_level(p, end);
    int      op;
    const char *q = *p;

    if (relop(&q, end, &op)) {
        BasValue b;

        *p = q;
        b = add_level(&q, end);
        *p = q;
        a = relational(op, &a, &b);
    } else {
        *p = q;
    }
    return a;
}

static BasValue and_level(const char **p, const char *end)
{
    BasValue a = relational_level(p, end);
    const char *q = *p;

    for (;;) {
        int      op;
        BasValue b;

        bas_skip_spaces(&q, end);
        if (bas_keyword(&q, end, "AND")) {
            op = OP_AND;
        } else if (bas_keyword(&q, end, "OR")) {
            op = OP_OR;
        } else if (bas_keyword(&q, end, "XOR")) {
            op = OP_XOR;
        } else if (bas_keyword(&q, end, "EQV")) {
            op = OP_EQV;
        } else if (bas_keyword(&q, end, "IMP")) {
            op = OP_IMP;
        } else {
            break;
        }
        *p = q;
        b = relational_level(&q, end);
        *p = q;
        a = bas_arith(op, &a, &b);
    }
    *p = q;
    return a;
}

BasValue bas_expr(const char **p, const char *end)
{
    return and_level(p, end);
}

BasValue bas_expr_primary(const char **p, const char *end)
{
    return primary(p, end);
}
