/* shell.c - the console layer, the REPL and the direct-mode commands.
 *
 * Input is read one character at a time from the platform so that Ctrl-Break
 * and a line-editing backspace work the same way on the board and in a host
 * test.  Output goes through con_* which also tracks the column used by POS,
 * CSRLIN and PRINT's zone padding. */
#include "basic.h"
#include <string.h>

/* ------------------------------------------------------------------ console */

#define CON_OUT  4096

static char con_out[CON_OUT];
static int  con_out_len;

/* Optional capture: the self test redirects the console into a buffer so it can
 * check what a program printed. */
static char    *con_cap_buf;
static unsigned con_cap_len;
static unsigned con_cap_max;

void con_capture_begin(char *buf, unsigned capacity)
{
    con_cap_buf = buf;
    con_cap_len = 0;
    con_cap_max = capacity;
    if (capacity)
        buf[0] = 0;
}

unsigned con_capture_end(void)
{
    unsigned n = con_cap_len;

    con_cap_buf = 0;
    con_cap_len = 0;
    con_cap_max = 0;
    return n;
}

static int con_col;                  /* column of the next character */
static int con_row;                  /* row for CSRLIN */
static int con_width = 80;

static int break_seen;               /* Ctrl-Break (0x1C) was typed */

void con_putc(char c)
{
    if (c == '\n') {
        con_col = 0;
        if (con_row < 2000)
            con_row++;
    } else if (c == '\r') {
        con_col = 0;
    } else if (c == '\b') {
        if (con_col > 0)
            con_col--;
    } else if (c == '\t') {
        con_col = (con_col + 8) & ~7;
    } else if ((unsigned char)c >= 32) {
        con_col++;
    }
    if (con_col >= con_width) {
        con_out[con_out_len++] = '\n';
        con_col = 0;
        con_row++;
    }
    con_out[con_out_len++] = c;
    if (con_out_len >= CON_OUT)
        con_flush();
    if (con_cap_buf) {
        if (con_cap_len + 1 < con_cap_max) {
            con_cap_buf[con_cap_len++] = c;
            con_cap_buf[con_cap_len] = 0;
        }
    }
}

void con_write(const char *s, unsigned n)
{
    while (n--)
        con_putc(*s++);
}

void con_puts(const char *s)
{
    while (*s)
        con_putc(*s++);
}

void con_flush(void)
{
    int n = con_out_len;

    con_out_len = 0;
    for (int i = 0; i < n; i++)
        bas_plat_putc(con_out[i]);
    bas_plat_flush();
}

void con_newline(void)
{
    con_putc('\r');
    con_putc('\n');
}

int con_getc(void)
{
    int c = bas_plat_getc();

    if (c == 0x1C)
        break_seen = 1;
    return c;
}

int con_getc_wait(void)
{
    for (;;) {
        int c = con_getc();

        if (c >= 0)
            return c;
        bas_plat_poll();
        if (break_seen)
            return -1;
    }
}

int con_break_pending(void)
{
    if (break_seen)
        return 1;
    bas_plat_poll();
    return break_seen;
}

void con_discard_input(void)
{
    break_seen = 0;
    while (con_getc() >= 0)
        ;
}

/* Read one line with line editing.  Ctrl-Break returns length 0 with the break
 * flag left set so the caller can report Break. */
void con_read_line(char *buf, int capacity, int *length, int echo)
{
    int n = 0;

    *length = 0;
    for (;;) {
        int c = con_getc_wait();

        if (c < 0) {                /* Ctrl-Break */
            if (echo)
                con_newline();
            *length = n;
            return;
        }
        if (c == '\r' || c == '\n') {
            if (echo)
                con_newline();
            break;
        }
        if (c == 0x7F || c == '\b') {
            if (n > 0) {
                n--;
                if (echo) {
                    con_putc('\b');
                    con_putc(' ');
                    con_putc('\b');
                    con_flush();
                }
            }
            continue;
        }
        if (c == 0x1B) {            /* Escape discards the line */
            n = 0;
            if (echo)
                con_newline();
            break;
        }
        if (c < 32)
            continue;
        if (n < capacity - 1) {
            buf[n++] = (char)c;
            if (echo) {
                con_putc((char)c);
                con_flush();
            }
        }
    }
    buf[n] = 0;
    *length = n;
}

/* ------------------------------------------------------- PRINT number rule */

/* GW-BASIC prints a leading space before a positive number and a space after
 * one, and a leading '-' for a negative number with no trailing space. */
void bas_print_number(const BasValue *v)
{
    char buf[48];
    int  negative;

    if (v->type == T_STR) {
        con_write(str_ptr(v->v.s), str_len(v->v.s));
        return;
    }
    negative = 0;
    if (v->type == T_INT)
        negative = v->v.i < 0;
    else if (v->type == T_SNG)
        negative = v->v.f < 0.0f;
    else
        negative = v->v.d < 0.0;

    bas_format_number(buf, sizeof(buf), v);
    if (negative) {
        con_puts(buf);
    } else {
        con_putc(' ');
        con_puts(buf);
        con_putc(' ');
    }
}

/* ------------------------------------------------------- small int to text */

void fmt_int(char *buf, int size, int value)
{
    char     tmp[12];
    int      n = 0, i;
    uint32_t u;
    int      negative = value < 0;

    u = negative ? (uint32_t)(-(value + 1)) + 1u : (uint32_t)value;
    do {
        tmp[n++] = (char)('0' + (int)(u % 10));
        u /= 10;
    } while (u && n < (int)sizeof(tmp));
    i = 0;
    if (negative && size > 1)
        buf[i++] = '-';
    while (n > 0 && i < size - 1)
        buf[i++] = tmp[--n];
    buf[i] = 0;
}

/* --------------------------------------------------------- error reporting */

int bas_error_armed;
bas_jmp_buf bas_error_jmp;

void bas_uncaught_error(int code)
{
    /* Nothing to return to: the REPL prints the message itself. */
    (void)code;
}

static void report_error(int code, int line)
{
    con_newline();
    con_puts(bas_error_text(code));
    if (line > 0) {
        con_puts(" in ");
        {
            char num[12];

            fmt_int(num, sizeof(num), line);
            con_puts(num);
        }
    }
    con_newline();
    con_flush();
}

/* --------------------------------------------------------------- the banner */

void bas_banner(void)
{
    con_puts("\r\nGW-BASIC for riscv-mini (BIOS payload)\r\n");
    con_puts("Memory: ");
    {
        char num[12];

        fmt_int(num, sizeof(num), (int)(BAS_HEAP_SIZE / 1024));
        con_puts(num);
    }
    con_puts(" Bytes free\r\n");
    con_puts("Ok\r\n");
    con_flush();
}

/* ------------------------------------------------------- program line input */

/* A line typed at the prompt is stored when it starts with a number.  Returns 1
 * when the text was a program line. */
static int store_program_line(const char *text, int len)
{
    const char *p = text, *end = text + len;
    BasValue    v;

    while (p < end && (*p == ' ' || *p == '\t'))
        p++;
    if (p >= end || !bas_scan_number(&p, end, &v))
        return 0;
    if (v.type != T_INT || v.v.i < 0 || (uint32_t)v.v.i > BAS_MAX_LINE)
        return 0;
    while (p < end && (*p == ' ' || *p == '\t'))
        p++;
    bas_line_insert((int)v.v.i, p, (int)(end - p));
    return 1;
}

/* ------------------------------------------------------------------- LIST */

/* The statement layer collects DATA statements when a run starts; editing the
 * program has to invalidate that list. */
extern void data_scan_reset(void);

void bas_list(int from, int to, int to_console)
{
    BasLine *l = bas_program;

    (void)to_console;
    while (l) {
        if ((int)l->number >= from && (int)l->number <= to) {
            char num[12];

            fmt_int(num, sizeof(num), (int)l->number);
            con_puts(num);
            con_putc(' ');
            con_write(l->text, l->len);
            con_newline();
        }
        l = l->next;
    }
    con_flush();
}

void bas_new(void)
{
    bas_program_clear();
    bas_vars_clear();
    bas_for_reset();
    data_scan_reset();
    bas_userfn_clear();
    bas_set_error_state(0, 0);
    bas_cont_valid = 0;
    bas_trace = 0;
}

void bas_renum(int first, int step)
{
    BasLine *l;
    int      number = first;

    if (first < 0 || step <= 0 || (uint32_t)step > BAS_MAX_LINE)
        bas_raise(E_ILLEGAL_FUNCTION_CALL);
    /* Renumbering rewrites every line, so collect the old text first. */
    for (l = bas_program; l; l = l->next) {
        if ((uint32_t)number > BAS_MAX_LINE)
            bas_raise(E_LINE_BUFFER_OVERFLOW);
        l->number = (uint16_t)number;
        number += step;
    }
    /* The list is still in the old order; rebuild it sorted. */
    {
        BasLine *head = bas_program, *next;

        bas_program = 0;
        while (head) {
            char     *text;
            uint16_t  len, number2;

            next = head->next;
            len = head->len;
            number2 = head->number;
            text = (char *)bas_alloc(len);
            if (!text)
                bas_raise(E_OUT_OF_MEMORY);
            memcpy(text, head->text, len);
            bas_free(head);
            /* bas_line_insert sorts by number, so this restores the order. */
            bas_line_insert((int)number2, text, len);
            bas_free(text);
            head = next;
        }
    }
    data_scan_reset();
}

void bas_delete(int from, int to)
{
    BasLine *l = bas_program;

    while (l) {
        BasLine *next = l->next;

        if ((int)l->number >= from && (int)l->number <= to)
            bas_line_delete((int)l->number);
        l = next;
    }
    data_scan_reset();
}

/* SAVE cannot work: the BIOS offers FILE_READ but no write service. */
void bas_save_unavailable(void)
{
    con_newline();
    con_puts(bas_error_text(E_UNAVAILABLE));
    con_puts(": this build has no file write service (LOAD works, SAVE does not)\r\n");
    con_flush();
}

/* LOAD "name": read a text program through the BIOS FILE_READ service. */
int bas_load(const char *name)
{
    static char buf[4096];
    uint32_t    offset = 0;
    int         total = 0;

    if (!name || !*name) {
        bas_raise(E_SYNTAX);
        return -1;
    }
    bas_program_clear();
    data_scan_reset();
    for (;;) {
        int n = bas_plat_file_read(name, offset, buf, sizeof(buf));

        if (n < 0) {
            con_newline();
            con_puts(bas_error_text(E_FILE_NOT_FOUND));
            con_newline();
            con_flush();
            return -1;
        }
        if (n == 0)
            break;
        offset += (uint32_t)n;
        total += n;
        /* Split the buffer into lines and store them. */
        {
            int i = 0;

            while (i < n) {
                char *start = buf + i;
                int   len = 0;

                while (i < n && buf[i] != '\n' && buf[i] != '\r') {
                    i++;
                    len++;
                }
                if (len > 0) {
                    char line[BAS_LINE_MAX];
                    int  copy = len;

                    if (copy > (int)BAS_LINE_MAX - 1)
                        copy = (int)BAS_LINE_MAX - 1;
                    memcpy(line, start, (size_t)copy);
                    line[copy] = 0;
                    if (!store_program_line(line, copy)) {
                        /* A line without a number is ignored while loading. */
                    }
                }
                while (i < n && (buf[i] == '\n' || buf[i] == '\r'))
                    i++;
            }
        }
        if ((uint32_t)n < sizeof(buf))
            break;
    }
    (void)total;
    return 0;
}

/* --------------------------------------------------------------------- REPL */

static void print_prompt(void)
{
    con_puts("Ok\r\n");
    con_flush();
}

void bas_repl(void)
{
    char line[BAS_LINE_MAX * 2];
    int  len;

    for (;;) {
        con_read_line(line, (int)sizeof(line), &len, 1);
        if (break_seen) {
            break_seen = 0;
            continue;
        }
        if (len == 0)
            continue;
        if (store_program_line(line, len)) {
            print_prompt();          /* GW-BASIC answers every line with Ok */
            continue;
        }

        /* Direct mode: upper-case the command part the same way a stored line
         * is stored, then execute it with the error handler armed. */
        bas_upper_line(line, len);
        bas_error_armed = 1;
        if (bas_setjmp(&bas_error_jmp) == 0) {
            bas_execute(line, len, 0, 1);
        } else {
            report_error(bas_err, bas_err_line);
        }
        bas_error_armed = 0;
        if (bas_running)
            bas_running = 0;
        print_prompt();
    }
}
