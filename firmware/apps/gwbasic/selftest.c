/* selftest.c - the built-in regression suite, run with the TESTS command.
 *
 * The repository has no registered host C compiler, so the interpreter checks
 * itself on the board: every case below states the expected GW-BASIC result as
 * a literal string, and the suite prints PASS/FAIL for each group.  The
 * expected values were produced with exact decimal arithmetic (Python's decimal
 * and math modules), not copied from another BASIC. */
#include "basic.h"
#include <string.h>

static int failures;
static int checks;

static void expect_str(const char *what, const char *got, const char *want)
{
    checks++;
    if (strcmp(got, want) == 0)
        return;
    failures++;
    con_puts("  FAIL ");
    con_puts(what);
    con_puts(": got \"");
    con_puts(got);
    con_puts("\" want \"");
    con_puts(want);
    con_puts("\"\r\n");
}

static void expect_num(const char *what, double got, double want, double tol)
{
    double err = got - want;

    if (err < 0)
        err = -err;
    checks++;
    if (err <= tol)
        return;
    failures++;
    con_puts("  FAIL ");
    con_puts(what);
    con_puts(": off by more than the stated tolerance\r\n");
}

/* The counter helper lives in shell.c; the self test shares it. */
extern void fmt_int(char *buf, int size, int value);

/* ------------------------------------------------------- number formatting */

static void test_format(void)
{
    char buf[64];
    BasValue v;

    con_puts("format\r\n");

    v = val_int(0);
    bas_format_number(buf, sizeof(buf), &v);
    expect_str("0", buf, "0");
    v = val_int(-32768);
    bas_format_number(buf, sizeof(buf), &v);
    expect_str("-32768", buf, "-32768");

    /* GW-BASIC prints seven significant digits for single precision and
     * sixteen for double, switches to the exponent form outside
     * -4 <= E <= sig-1, and writes no leading zero below 1. */
    bas_format_double(buf, sizeof(buf), 1.0, T_SNG);
    expect_str("1", buf, "1");
    bas_format_double(buf, sizeof(buf), 1000000.0, T_SNG);
    expect_str("1000000", buf, "1000000");
    bas_format_double(buf, sizeof(buf), 10000000.0, T_SNG);
    expect_str("1E+07", buf, "1E+07");
    bas_format_double(buf, sizeof(buf), 12345675.0, T_SNG);
    expect_str("1.234568E+07", buf, "1.234568E+07");
    bas_format_double(buf, sizeof(buf), 0.0001, T_SNG);
    expect_str(".0001", buf, ".0001");
    bas_format_double(buf, sizeof(buf), 0.00001, T_SNG);
    expect_str("1E-05", buf, "1E-05");
    bas_format_double(buf, sizeof(buf), 1.0 / 3.0, T_SNG);
    expect_str("1/3 single", buf, ".3333333");
    bas_format_double(buf, sizeof(buf), 1.0 / 3.0, T_DBL);
    expect_str("1/3 double", buf, ".3333333333333333");
    bas_format_double(buf, sizeof(buf), 1234567890123456.0, T_DBL);
    expect_str("16 digits", buf, "1234567890123456");
    bas_format_double(buf, sizeof(buf), 1e16, T_DBL);
    expect_str("1E+16", buf, "1E+16");
    bas_format_double(buf, sizeof(buf), 1e300, T_DBL);
    expect_str("1E+300", buf, "1E+300");
    bas_format_double(buf, sizeof(buf), -0.5, T_SNG);
    expect_str("-.5", buf, "-.5");
    bas_format_double(buf, sizeof(buf), 2.5, T_SNG);
    expect_str("2.5", buf, "2.5");
    bas_format_double(buf, sizeof(buf), 123456.7, T_SNG);
    expect_str("123456.7", buf, "123456.7");
}

/* --------------------------------------------------------------- arithmetic */

static void test_arith(void)
{
    char     buf[64];
    BasValue a, b, r;

    con_puts("arith\r\n");

    a = val_int(3);
    b = val_int(4);
    r = bas_arith(OP_ADD, &a, &b);
    val_release(&a);
    val_release(&b);
    bas_format_number(buf, sizeof(buf), &r);
    expect_str("3+4", buf, "7");
    val_release(&r);

    a = val_int(7);
    b = val_int(2);
    r = bas_arith(OP_IDIV, &a, &b);
    bas_format_number(buf, sizeof(buf), &r);
    expect_str("7\\2", buf, "3");
    val_release(&r);
    r = bas_arith(OP_MOD, &a, &b);
    bas_format_number(buf, sizeof(buf), &r);
    expect_str("7 MOD 2", buf, "1");
    val_release(&r);
    val_release(&a);
    val_release(&b);

    a = val_int(2);
    b = val_int(10);
    r = bas_arith(OP_POW, &a, &b);
    bas_format_number(buf, sizeof(buf), &r);
    expect_str("2^10", buf, "1024");
    val_release(&r);
    val_release(&a);
    val_release(&b);

    a = val_int(5);
    b = val_int(3);
    r = bas_arith(OP_AND, &a, &b);
    bas_format_number(buf, sizeof(buf), &r);
    expect_str("5 AND 3", buf, "1");
    val_release(&r);
    r = bas_arith(OP_OR, &a, &b);
    bas_format_number(buf, sizeof(buf), &r);
    expect_str("5 OR 3", buf, "7");
    val_release(&r);
    r = bas_arith(OP_XOR, &a, &b);
    bas_format_number(buf, sizeof(buf), &r);
    expect_str("5 XOR 3", buf, "6");
    val_release(&r);
    /* EQV and IMP are 16-bit bitwise operators: 5 EQV 3 = NOT(5 XOR 3) = -7
     * and 5 IMP 3 = (NOT 5) OR 3 = -5, exactly as GW-BASIC prints them. */
    r = bas_arith(OP_EQV, &a, &b);
    bas_format_number(buf, sizeof(buf), &r);
    expect_str("5 EQV 3", buf, "-7");
    val_release(&r);
    r = bas_arith(OP_IMP, &a, &b);
    bas_format_number(buf, sizeof(buf), &r);
    expect_str("5 IMP 3", buf, "-5");
    val_release(&r);
    val_release(&a);
    val_release(&b);

    /* Integer overflow is error 6, not a wrap.  The suite runs with no handler
     * armed, so bas_raise records the code and returns. */
    a = val_int(32767);
    b = val_int(1);
    bas_err = 0;
    r = bas_arith(OP_ADD, &a, &b);
    checks++;
    if (bas_err != E_OVERFLOW) {
        failures++;
        con_puts("  FAIL 32767+1 did not raise error 6\r\n");
    }
    bas_err = 0;
    val_release(&r);
    val_release(&a);
    val_release(&b);
}

/* ------------------------------------------------------------------ strings */

/* The string builtins are checked by evaluating them directly; the expected
 * values are the GW-BASIC results. */
static void check_builtin(const char *src, const char *want)
{
    char        buf[512];
    const char *p = src, *end = src + strlen(src);
    BasValue    v;

    v = bas_expr(&p, end);
    if (v.type == T_STR) {
        uint32_t len = str_len(v.v.s);

        if (len >= sizeof(buf))
            len = sizeof(buf) - 1;
        memcpy(buf, str_ptr(v.v.s), len);
        buf[len] = 0;
    } else {
        bas_format_number(buf, sizeof(buf), &v);
    }
    expect_str(src, buf, want);
    val_release(&v);
}

static void test_strings(void)
{
    con_puts("strings\r\n");

    check_builtin("LEFT$(\"HELLO WORLD\",5)", "HELLO");
    check_builtin("RIGHT$(\"HELLO WORLD\",5)", "WORLD");
    check_builtin("MID$(\"HELLO WORLD\",7,5)", "WORLD");
    check_builtin("MID$(\"ABCDEFG\",2,3)", "BCD");
    check_builtin("CHR$(65)+CHR$(66)", "AB");
    check_builtin("STRING$(3,\"*\")", "***");
    check_builtin("SPACE$(2)", "  ");
    check_builtin("UCASE$(\"ab\")+LCASE$(\"CD\")", "ABcd");
    check_builtin("LEN(\"HELLO WORLD\")", "11");
    check_builtin("ASC(\"A\")", "65");
    check_builtin("VAL(\"12.5\")+VAL(\".5\")", "13");
    check_builtin("INSTR(\"HELLO WORLD\",\"W\")", "7");
    check_builtin("HEX$(255)", "FF");
    check_builtin("OCT$(8)", "10");
    check_builtin("STR$(123)", " 123");
    check_builtin("STR$(-45.5)", "-45.5");
    check_builtin("STR$(2^10)", " 1024");
    check_builtin("STR$(1/3)", " .3333333");
    check_builtin("STR$(-1/3)", "-.3333333");
    check_builtin("VAL(\"3.5E2\")", "350");
    check_builtin("VAL(\"-7\")", "-7");
    check_builtin("VAL(\"FF\")", "0");    /* VAL stops at the first non-digit */
    check_builtin("\"A\"+\"B\"+\"C\"", "ABC");
    check_builtin("RIGHT$(\"ABCDEFG\",3)", "EFG");
}

static void expect_rel(const char *what, double got, double want, double rel)
{
    double err = got - want;
    double mag = want < 0.0 ? -want : want;

    if (err < 0)
        err = -err;
    checks++;
    if (err <= rel * (mag > 0.0 ? mag : 1.0))
        return;
    failures++;
    con_puts("  FAIL ");
    con_puts(what);
    con_puts(": out of tolerance\r\n");
}

/* The expected numbers were produced with Python's math module, so they are the
 * doubles nearest the true results.  The tolerances are relative: the kernels
 * are good to a few units in the last place. */
static void test_math(void)
{
    con_puts("math\r\n");
    expect_rel("exp(1)", bas_exp(1.0), 2.7182818284590450908, 4e-16);
    expect_rel("exp(-1)", bas_exp(-1.0), 0.36787944117144233402, 4e-16);
    expect_rel("exp(700)", bas_exp(700.0), 1.0142320547350045e304, 1e-14);
    expect_rel("log(2)", bas_log(2.0), 0.69314718055994528623, 4e-16);
    expect_rel("log(88)", bas_log(88.0), 4.4773368144782068612, 4e-16);
    expect_rel("sqr(2)", bas_sqr(2.0), 1.4142135623730951455, 4e-16);
    expect_rel("sqr(1e20)", bas_sqr(1e20), 1e10, 1e-15);
    expect_rel("atn(1)", bas_atn(1.0), 0.785398163397448279, 4e-16);
    expect_rel("atn(3.5)", bas_atn(3.5), 1.2924966677897853362, 4e-16);
    expect_num("pow(2,10)", bas_pow(2.0, 10.0), 1024.0, 0.0);
    expect_rel("pow(1.1,20)", bas_pow(1.1, 20.0), 6.7274999493256109062, 1e-15);
    expect_rel("sin(1)", bas_sin(1.0), 0.84147098480789650488, 1e-15);
    expect_rel("cos(1)", bas_cos(1.0), 0.54030230586813976501, 1e-15);
    expect_rel("sin(1000)", bas_sin(1000.0), 0.82687954053200252158, 1e-14);
    expect_rel("cos(1000)", bas_cos(1000.0), 0.56237907629070293947, 1e-14);
    expect_rel("tan(0.5)", bas_tan(0.5), 0.54630248984379048416, 1e-15);
}

/* --------------------------------------------------------------- PRINT USING */

static void test_using(void)
{
    char     buf[64];
    BasValue v;

    con_puts("using\r\n");

    /* The field is as wide as the image: "######.##" is nine columns. */
    v = val_sng(1234.5678f);
    bas_format_using(buf, sizeof(buf), &v, 6, 2, 0);
    expect_str("######.##", buf, "  1234.57");
    v = val_int(-42);
    bas_format_using(buf, sizeof(buf), &v, 4, 0, 0);
    expect_str("####", buf, " -42");
    v = val_sng(3.5f);
    bas_format_using(buf, sizeof(buf), &v, 4, 2, 1);
    expect_str("+####.##", buf, "  +3.50");
    v = val_sng(7.5f);
    bas_format_using(buf, sizeof(buf), &v, 3, 2, 2);
    expect_str("***.##", buf, "**7.50");
    /* 12.34 needs two integer digits, so a one-digit image cannot hold it. */
    v = val_sng(12.34f);
    checks++;
    if (bas_format_using(buf, sizeof(buf), &v, 1, 2, 0) <= 4) {
        failures++;
        con_puts("  FAIL narrow image accepted\r\n");
    }
    val_release(&v);
}

/* ------------------------------------------------------------------ program */

static void test_program(void)
{
    con_puts("program\r\n");
    /* Line editing, RENUM and DELETE are checked through the interpreter's own
     * data structures. */
    bas_new();
    bas_line_insert(10, "PRINT 1", 7);
    bas_line_insert(20, "PRINT 2", 7);
    bas_line_insert(15, "PRINT 15", 8);
    checks++;
    if (bas_program_lines() != 3) {
        failures++;
        con_puts("  FAIL line count after insert\r\n");
    }
    checks++;
    if (!bas_line_find(15) || !bas_line_find(20)) {
        failures++;
        con_puts("  FAIL line lookup\r\n");
    }
    bas_line_delete(15);
    checks++;
    if (bas_line_find(15)) {
        failures++;
        con_puts("  FAIL delete did not remove the line\r\n");
    }
    bas_renum(100, 10);
    checks++;
    if (!bas_line_find(100) || !bas_line_find(110)) {
        failures++;
        con_puts("  FAIL RENUM\r\n");
    }
    bas_new();
}

/* --------------------------------------------------------------------- run */

/* --------------------------------------------------------------------- run */

/* The statement layer is checked end to end: a small program is typed in, run
 * with the console captured, and its transcript compared. */
static struct { int number; const char *text; } run_program[] = {
    { 10, "FOR I=1 TO 3: PRINT I;: NEXT I: PRINT" },
    { 20, "IF 2>1 THEN PRINT \"THEN\" ELSE PRINT \"ELSE\"" },
    { 30, "GOSUB 100" },
    { 40, "DIM A(3): A(2)=7: PRINT A(2)" },
    { 50, "DATA 1,2: READ P,Q: PRINT P+Q" },
    { 60, "DEF FNZ(V)=V*2: PRINT FNZ(21)" },
    { 70, "WHILE W<2: W=W+1: WEND: PRINT \"W\";W" },
    { 80, "PRINT \"END\"" },
    { 85, "END" },
    { 100, "PRINT \"SUB\": RETURN" },
};

static void test_run(void)
{
    static char captured[1024];
    unsigned    i;

    con_puts("run\r\n");
    bas_new();
    for (i = 0; i < sizeof(run_program) / sizeof(run_program[0]); i++) {
        const char *text = run_program[i].text;
        int         len = (int)strlen(text);

        bas_line_insert(run_program[i].number, text, len);
    }
    checks++;
    if (bas_program_lines() != (int)(sizeof(run_program) / sizeof(run_program[0]))) {
        failures++;
        con_puts("  FAIL program was not stored\r\n");
    }
    con_capture_begin(captured, sizeof(captured));
    bas_run_program(0, 0);
    (void)con_capture_end();
    expect_str("statement program",
               captured,
               " 1  2  3 \r\nTHEN\r\nSUB\r\n 7 \r\n 3 \r\n 42 \r\nW 2 \r\nEND\r\n");
    bas_new();
}

void bas_selftest(void)
{
    int armed = bas_error_armed;

    bas_error_armed = 0;             /* the suite inspects bas_err itself */
    con_puts("\r\nGW-BASIC self test\r\n");
    test_format();
    test_arith();
    test_strings();
    test_math();
    test_using();
    test_program();
    test_run();
    con_puts(failures ? "FAILURES: " : "ALL TESTS PASSED (");
    if (failures) {
        char num[12];

        fmt_int(num, sizeof(num), failures);
        con_puts(num);
        con_puts(" of ");
        fmt_int(num, sizeof(num), checks);
        con_puts(num);
        con_puts(" checks)\r\n");
    } else {
        char num[12];

        fmt_int(num, sizeof(num), checks);
        con_puts(num);
        con_puts(" checks)\r\n");
    }
    con_flush();
    bas_error_armed = armed;
}
