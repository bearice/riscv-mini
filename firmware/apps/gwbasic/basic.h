/* GW-BASIC interpreter core -- shared declarations.
 *
 * The core is platform independent: firmware/apps/gwbasic/plat_bios.c supplies
 * the BIOS ecall backend, tests/gwbasic_test.c supplies a stdio backend. */
#pragma once
#include <stdint.h>
#include <stddef.h>

/* ------------------------------------------------------------------ limits */
#define BAS_LINE_MAX   255u          /* characters in one stored program line */
#define BAS_HEAP_SIZE  (4u * 1024u * 1024u)
#define BAS_MAX_VARS   1024
#define BAS_MAX_FOR    32
#define BAS_MAX_GOSUB  64
#define BAS_MAX_WHILE  32
#define BAS_MAX_DIMS   4
#define BAS_STR_MAX    32767         /* longest string GW-BASIC accepts */
#define BAS_CON_IN     256
#define BAS_MAX_LINE   65529u        /* highest line number a program may use */

/* ------------------------------------------------------- platform backbone */
void     bas_plat_putc(char c);              /* console byte, unbuffered */
void     bas_plat_flush(void);               /* drain transport buffer */
void     bas_plat_poll(void);                /* service BIOS devices */
uint32_t bas_plat_ms(void);                  /* monotonic milliseconds */
int      bas_plat_getc(void);                /* -1 when idle */
/* Installed by the entry point: bas_plat_exit returns there.  The arm call
 * returns 0 the first time and non-zero after bas_plat_exit. */
int      bas_plat_exit_arm(void);
void     bas_plat_exit(void) __attribute__((noreturn));
/* text(0)/graphics(1) switch; returns 0 when accepted */
int      bas_plat_video(int graphics);
/* frame buffer of the graphics mode, or 0 when video is unavailable */
uint16_t *bas_plat_framebuffer(int *width, int *height, int *stride);
void     bas_plat_present(void);             /* show the frame buffer */
/* read one file from SD; returns bytes read or -1 */
int      bas_plat_file_read(const char *name, uint32_t offset, void *dst, uint32_t capacity);
/* OUT statement and INP()/WAIT: the board has no I/O ports, so the platform
 * answers -1 (no device) and ignores writes. */
void     bas_plat_out(int port, int value);
int      bas_plat_in(int port);
/* Raw BIOS ecall service for the BIOS statement: n is the function number and
 * args[] holds a0..a5.  A host harness makes this a no-op. */
void     bas_bios_call(int n, const uint32_t *args);
/* Board only: hand the BIOS description from payload_main to the platform
 * layer.  Not needed by a host harness. */
struct bios_info;
void     bas_plat_set_info(const struct bios_info *src);

/* setjmp/longjmp: RISC-V assembly in setjmp.S, libc in the host tests. */
typedef struct { uint32_t regs[16]; } bas_jmp_buf;
int      bas_setjmp(bas_jmp_buf *b);
void     bas_longjmp(bas_jmp_buf *b, int value) __attribute__((noreturn));

/* ------------------------------------------------------------ value model */
enum { T_INT = 1, T_SNG, T_DBL, T_STR };

typedef struct BasStr { uint32_t refs; uint32_t len; char data[]; } BasStr;

typedef struct BasValue {
    uint8_t type;
    union { int32_t i; float f; double d; BasStr *s; } v;
} BasValue;

/* heap: size class allocator over BAS_HEAP_SIZE bytes of BSS */
void      bas_heap_reset(void);
void     *bas_alloc(uint32_t bytes);
void      bas_free(void *block);

/* refcounted strings; a NULL BasStr* is the empty string */
BasStr   *str_alloc(uint32_t len);
BasStr   *str_from(const char *data, uint32_t len);
BasStr   *str_retain(BasStr *s);
void      str_release(BasStr *s);
uint32_t  str_len(const BasStr *s);
const char *str_ptr(const BasStr *s);
BasStr   *str_concat(const BasStr *a, const BasStr *b);

BasValue  val_int(int32_t i);
BasValue  val_sng(float f);
BasValue  val_dbl(double d);
BasValue  val_str(BasStr *s);                /* takes ownership of s */
BasValue  val_str_ref(const BasStr *s);      /* makes a reference */
BasValue  val_empty(void);
void      val_release(BasValue *v);
void      val_set(BasValue *dst, const BasValue *src);   /* releases dst */
double    val_number(const BasValue *v);
int32_t   val_integer(const BasValue *v);    /* rounds, raises Overflow */
/* Read a value as a number of its own kind: an integer stays an integer, a
 * single stays a single.  Used by the statement layer for line numbers,
 * coordinates and colours. */
int32_t   val_int_of(const BasValue *v);
float     val_sng_of(const BasValue *v);
double    val_dbl_of(const BasValue *v);
int       val_truth(const BasValue *v);
int       val_compare(const BasValue *a, const BasValue *b);
int       val_round_even(double d);  /* CINT/rounding used by the builtins */
BasValue  val_negate(const BasValue *a);
void      val_promote(BasValue *v, int type);
/* operators: the ASCII ones use their own code, the rest are numbered */
enum { OP_ADD = '+', OP_SUB = '-', OP_MUL = '*', OP_DIV = '/', OP_IDIV = '\\', OP_POW = '^',
       OP_MOD = 1000, OP_NEG, OP_NOT, OP_AND, OP_OR, OP_XOR, OP_EQV, OP_IMP,
       OP_EQ, OP_NE, OP_LT, OP_GT, OP_LE, OP_GE };
BasValue  bas_arith(int op, const BasValue *a, const BasValue *b);
BasValue  val_bitnot(const BasValue *a);
uint32_t  bas_heap_used(void);
uint32_t  bas_heap_free(void);

/* libm-free elementary functions (firmware/apps/gwbasic/basmath.c) */
double bas_sqr(double x);
double bas_exp(double x);
double bas_log(double x);
double bas_sin(double x);
double bas_cos(double x);
double bas_tan(double x);
double bas_atn(double x);
double bas_pow(double x, double y);
/* GW-BASIC style text for a value: numbers get a sign column and no padding */
int       bas_format_number(char *buf, int size, const BasValue *value);
int       bas_format_double(char *buf, int size, double value, int type);

/* ----------------------------------------------------------- error handling */
enum {
    E_NEXT_WITHOUT_FOR = 1, E_SYNTAX = 2, E_RETURN_WITHOUT_GOSUB = 3, E_OUT_OF_DATA = 4,
    E_ILLEGAL_FUNCTION_CALL = 5, E_OVERFLOW = 6, E_OUT_OF_MEMORY = 7,
    E_UNDEFINED_LINE = 8, E_SUBSCRIPT = 9, E_DUPLICATE_DEF = 10, E_DIV_ZERO = 11,
    E_ILLEGAL_DIRECT = 12, E_TYPE_MISMATCH = 13, E_OUT_OF_STRING_SPACE = 14,
    E_STRING_TOO_LONG = 15, E_STRING_COMPLEX = 16, E_CANT_CONTINUE = 17,
    E_UNDEFINED_FUNCTION = 18, E_NO_RESUME = 19, E_RESUME_WITHOUT_ERROR = 20,
    E_UNPRINTABLE = 21, E_MISSING_OPERAND = 22, E_LINE_BUFFER_OVERFLOW = 23,
    E_DEVICE_TIMEOUT = 24, E_DEVICE_FAULT = 25, E_FOR_WITHOUT_NEXT = 26,
    E_OUT_OF_PAPER = 27, E_WHILE_WITHOUT_WEND = 29, E_WEND_WITHOUT_WHILE = 30,
    E_FIELD_OVERFLOW = 50, E_INTERNAL = 51, E_BAD_FILE_NUMBER = 52,
    E_FILE_NOT_FOUND = 53, E_BAD_FILE_MODE = 54, E_FILE_ALREADY_OPEN = 55,
    E_DEVICE_IO = 57, E_FILE_EXISTS = 58, E_DISK_FULL = 61, E_INPUT_PAST_END = 62,
    E_BAD_RECORD = 63, E_BAD_FILE_NAME = 64, E_DIRECT_IN_FILE = 66, E_TOO_MANY_FILES = 67,
    E_DEVICE_UNAVAILABLE = 68, E_COMM_BUFFER = 69, E_PERMISSION = 70,
    E_DISK_NOT_READY = 71, E_DISK_MEDIA = 72, E_ADVANCED = 73, E_PATH_FILE = 75,
    E_PATH_NOT_FOUND = 76,
    E_BREAK = 90, E_UNAVAILABLE = 91
};
extern int bas_err;                 /* code of the failure being reported */
extern int bas_err_line;            /* program line, 0 in direct mode */
const char *bas_error_text(int code);
void bas_raise(int code);
void bas_raise_line(int code, int line);
/* armed by the interpreter shell; uncaught errors are reported and dropped */
extern bas_jmp_buf bas_error_jmp;
extern int         bas_error_armed;
void bas_uncaught_error(int code);

/* --------------------------------------------------------------- program */
typedef struct BasLine {
    uint16_t number;
    uint16_t len;
    struct BasLine *next;
    char text[];                    /* no line number, no terminator requirement */
} BasLine;

extern BasLine *bas_program;        /* sorted by line number */
extern BasLine *bas_current;        /* line being executed / listed */
BasLine *bas_line_find(int number);
void     bas_line_insert(int number, const char *text, int len);
int      bas_line_delete(int number);
void     bas_program_clear(void);
int      bas_program_lines(void);

/* ------------------------------------------------------------- variables */
typedef struct BasVar {
    char     key[4];                /* two significant characters + type code */
    uint8_t  type;
    uint8_t  is_array;
    uint8_t  used;                  /* ever read or assigned as a scalar */
    BasValue value;                 /* scalar storage */
    struct BasArray *array;
} BasVar;

typedef struct BasArray {
    uint8_t  type;
    uint8_t  ndims;
    int32_t  count[BAS_MAX_DIMS];
    int32_t  total;
    BasValue data[];
} BasArray;

BasVar   *bas_var(const char *name, int len, int create);
/* The current value of a scalar variable, for the self test. */
BasValue  bas_var_value(const char *name);
void      bas_vars_clear(void);
void      bas_def_type(int first, int last, int type);
/* DIM statement: parses "name(bounds){,name(bounds)}" and creates the arrays.
 * An undimensioned reference such as A(I) gets the default 0..10 bounds. */
void      bas_dim_statement(const char **p, const char *end);
/* Low-level creator: counts[] holds the element count of each dimension. */
BasArray *bas_array_dim(BasVar *var, int ndims, const int32_t *counts);
void      bas_array_clear(BasArray *array);
void      bas_def_type_range(const char **p, const char *end, int type);
extern uint8_t bas_option_base;     /* 0 by default, 1 after OPTION BASE 1 */
int       bas_name_type(const char *name, int len);

/* ---------------------------------------------------------- lexer helpers */
int   bas_is_name_start(int c);
int   bas_is_name_char(int c);
void  bas_skip_spaces(const char **p, const char *end);
int   bas_keyword(const char **p, const char *end, const char *word);
int   bas_keyword_at(const char *p, const char *end, const char *word);
int   bas_read_name(const char **p, const char *end, char out[16]);
int   bas_upcase(int c);
void  bas_upper_line(char *text, int len);
int   bas_scan_number(const char **p, const char *end, BasValue *out);

/* ---------------------------------------------------------------- console */
void con_putc(char c);
void con_write(const char *s, unsigned n);
void con_puts(const char *s);
void con_flush(void);
void con_newline(void);
int  con_getc(void);                   /* -1 when idle */
int  con_getc_wait(void);              /* blocks, honours Ctrl-Break */
void con_read_line(char *buf, int capacity, int *length, int echo);
int  con_break_pending(void);
void con_discard_input(void);

void bas_print_number(const BasValue *v);   /* PRINT sign-column rule */

/* Redirect the console into a buffer (used by the built-in self test). */
void     con_capture_begin(char *buf, unsigned capacity);
unsigned con_capture_end(void);

/* ------------------------------------------------------------ expressions */
BasValue bas_expr(const char **p, const char *end);
BasValue bas_expr_primary(const char **p, const char *end);

/* ------------------------------------------------------------- statements */
extern int bas_running;             /* a program is executing */
extern int bas_trace;               /* TRON */
extern int bas_break;               /* Ctrl-Break seen, checked between lines */
extern int bas_cont_valid;

/* A statement asked control to move.  The driver resolves it against the line
 * list; JUMP_RESUME returns to a saved (line, offset) pair. */
enum { JUMP_NONE = 0, JUMP_LINE, JUMP_RESUME };

extern int bas_jump_pending;
extern int bas_jump_kind;
extern int bas_jump_target;                /* line number for JUMP_LINE */
extern int bas_jump_resume_line;           /* line for JUMP_RESUME */
extern int bas_jump_offset;                /* offset for JUMP_RESUME */

void bas_jump_line(int number);
void bas_jump_line_offset(int number, int offset);
void bas_jump_line_to(BasLine *line, int offset);

/* Run the statements in one line, stopping at an ELSE of an enclosing IF when
 * in_if is set.  Returns 0 at the end of the line, 1 when an ELSE was reached,
 * 2 when a jump is pending. */
int  bas_run_sequence(const char **p, const char *end, int in_if);
void bas_exec_statement(const char **p, const char *end);

void bas_execute(const char *text, int len, int line_number, int direct);
void bas_run_program(int start_line, int from_continue);

/* Control stacks, shared by the driver and NEW. */
void bas_gosub_push(int line, int offset);
void bas_for_reset(void);
void bas_data_reset(void);
void bas_userfn_clear(void);
void bas_def_fn(const char **p, const char *end);

/* PRINT USING field: renders one value into an image with `digits` integer and
 * `dec` fraction positions.  flags: 1 force sign, 2 leading stars, 4 dollars.
 * Returns the length written, or more than the image width when it will not
 * fit. */
int  bas_format_using(char *buf, int size, const BasValue *v, int digits, int dec,
                      int flags);

/* --------------------------------------------------------------- builtins */
/* A builtin is looked up by its upper-case name; string functions carry the
 * '$' in the name.  min_args < 0 means the parentheses are optional, e.g.
 * RND and TIMER may be written bare. */
typedef struct BasBuiltin {
    const char *name;
    int8_t      type;               /* result type */
    int8_t      min_args;           /* -1: parentheses optional */
    int8_t      max_args;
} BasBuiltin;

/* Argument forms: bas_builtin_call is the normal one.  The statement layer
 * uses bas_builtin_bare for a bare name with no parentheses (INKEY$, TIMER)
 * and bas_builtin_take for the assignment-style builtins that write into a
 * variable (INKEY$ = ..., INPUT$ = ..., LOC$ = ...); it returns 0 when the
 * name is not one of those. */
const BasBuiltin *bas_builtin_find(const char *name);
BasValue bas_builtin_call(const BasBuiltin *b, const char *name,
                          const BasValue *args, int nargs);
BasValue bas_builtin_bare(const BasBuiltin *b);
int      bas_builtin_take(const BasBuiltin *b, BasValue *slot);
void     bas_randomize(int seed, int have_seed);
void     bas_set_error_state(int code, int line);
int      bas_get_error(void);
int      bas_get_erl(void);
void     bas_note_output(const char *s, unsigned n);
int      bas_pos_value(void);
void     bas_set_width(int wide);
extern uint32_t bas_rnd_seed;
double bas_rnd(void);

/* --------------------------------------------------------------- graphics */
/* One hardware mode: the 480x272 RGB565 panel.  SCREEN 1 and SCREEN 2 both
 * select it; coordinates are panel coordinates, colours are CGA 16-colour. */
#define GFX_WIDTH   480
#define GFX_HEIGHT  272
#define TEXT_COLS   80
#define TEXT_ROWS   34

extern int bas_graphics;            /* 0 = BIOS text, 1 = panel graphics */
void bas_screen(int mode);          /* SCREEN statement */
void bas_cls(void);
void bas_color(int fg, int bg);
void bas_pset(double x, double y, int color, int preset);
void bas_line_draw(double x1, double y1, double x2, double y2, int color,
                   int style, int box, int fill);
void bas_circle_draw(double x, double y, double r, int color,
                     double start, double end, double aspect);
void bas_paint_at(double x, double y, int color, int border);
int  bas_point(double x, double y);
int  bas_screen_function(int row, int col, int mode);
void gfx_putc(char c);              /* text drawn into the panel */
void gfx_cls(int color);
void gfx_present(void);
void gfx_locate(int row, int col);  /* col < 0 moves the row only */

/* Last LINE/PSET endpoint, so that LINE -(x,y) continues from it. */
extern int bas_last_x;
extern int bas_last_y;

/* The statement layer's DATA scan; editing the program must invalidate it. */
void data_scan_reset(void);

/* ---------------------------------------------------------------- machine */
void bas_banner(void);
void bas_repl(void);
void bas_list(int from, int to, int to_console);
void bas_new(void);
void bas_renum(int first, int step);
void bas_delete(int from, int to);
void bas_save_unavailable(void);
int  bas_load(const char *name);
void bas_selftest(void);            /* built-in regression suite (TESTS) */
