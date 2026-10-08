/* screen.c - the display back end.
 *
 * The board has one bitmap mode: the 480x272 RGB565 panel.  SCREEN 0 keeps the
 * BIOS text console; SCREEN 1/2 switch the panel on and the interpreter draws
 * text and graphics into a frame buffer it pushes with gfx_present().
 *
 * Colours are GW-BASIC's 16 CGA entries mapped onto the panel. */
#include "basic.h"
#include <string.h>

/* ------------------------------------------------------------------ palette */

static const uint16_t cga16[16] = {
    0x0000,   /* 0  black       */
    0x0015,   /* 1  blue        */
    0x1400,   /* 2  green       */
    0x1415,   /* 3  cyan        */
    0xA000,   /* 4  red         */
    0xA015,   /* 5  magenta     */
    0xA800,   /* 6  brown       */
    0xBDF7,   /* 7  light gray  */
    0x4210,   /* 8  dark gray   */
    0x051F,   /* 9  light blue  */
    0x2BE0,   /* 10 light green */
    0x2BFF,   /* 11 light cyan  */
    0xF800,   /* 12 light red   */
    0xF81F,   /* 13 light mag.  */
    0xFC00,   /* 14 yellow      */
    0xFFFF,   /* 15 white       */
};

int bas_graphics;

static uint16_t *fb;
static int       fb_w, fb_h, fb_stride;
static int       fg_color = 15, bg_color = 0;
static int       text_col, text_row;
static int       last_pen_x, last_pen_y;

/* The 5x7 cell font, shared with the firmware canvas. */
#include "../../common/font5x7.h"

#define CELL_W 6                       /* 5 dots + 1 column gap */
#define CELL_H 8                       /* 7 rows + 1 row gap    */
#define PANEL_COLS (GFX_WIDTH / CELL_W)
#define PANEL_ROWS (GFX_HEIGHT / CELL_H)

static uint16_t rgb565(int cga)
{
    if (cga < 0)
        cga = 0;
    if (cga > 15)
        cga &= 15;
    return cga16[cga];
}

/* ------------------------------------------------------------- frame buffer */

static int fb_attach(void)
{
    if (fb)
        return 1;
    fb = bas_plat_framebuffer(&fb_w, &fb_h, &fb_stride);
    return fb != 0;
}

static void fb_put(int x, int y, uint16_t color)
{
    if (x < 0 || y < 0 || x >= fb_w || y >= fb_h)
        return;
    fb[y * fb_stride + x] = color;
}

static uint16_t fb_get(int x, int y)
{
    if (x < 0 || y < 0 || x >= fb_w || y >= fb_h)
        return 0;
    return fb[y * fb_stride + x];
}

void gfx_cls(int color)
{
    int x, y;
    uint16_t c = rgb565(color);

    if (!fb_attach())
        return;
    for (y = 0; y < fb_h; y++)
        for (x = 0; x < fb_w; x++)
            fb[y * fb_stride + x] = c;
    last_pen_x = 0;
    last_pen_y = 0;
}

void gfx_present(void)
{
    if (bas_graphics)
        bas_plat_present();
}

/* ------------------------------------------------------------- text on panel */

static void draw_glyph(int x, int y, const uint8_t *bits, uint16_t fg, uint16_t bg)
{
    int r, c;

    for (r = 0; r < 7; r++) {
        uint8_t row = bits[r];

        for (c = 0; c < 5; c++)
            fb_put(x + c, y + r, (row & (1u << (4 - c))) ? fg : bg);
    }
}

void gfx_putc(char ch)
{
    const uint8_t *bits;

    if (!bas_graphics) {
        con_putc(ch);
        return;
    }
    if (!fb_attach())
        return;
    if (ch == '\r')
        return;
    if (ch == '\n') {
        text_col = 0;
        text_row++;
        if (text_row >= PANEL_ROWS)
            text_row = 0;             /* the panel does not scroll */
        return;
    }
    if (ch == '\b') {
        if (text_col > 0)
            text_col--;
        return;
    }
    if ((unsigned char)ch < 32)
        return;
    if (text_col >= PANEL_COLS) {
        text_col = 0;
        text_row++;
        if (text_row >= PANEL_ROWS)
            text_row = 0;
    }
    bits = font5x7_glyph((unsigned char)ch);    draw_glyph(text_col * CELL_W, text_row * CELL_H, bits,
               rgb565(fg_color), rgb565(bg_color));
    text_col++;
}

void gfx_locate(int row, int col)
{
    if (row >= 0) {
        text_row = row;
        if (text_row >= PANEL_ROWS)
            text_row = PANEL_ROWS - 1;
    }
    if (col >= 0) {
        text_col = col;
        if (text_col >= PANEL_COLS)
            text_col = PANEL_COLS - 1;
    }
}

/* --------------------------------------------------------------- statements */

void bas_screen(int mode)
{
    if (mode < 0 || mode > 3)
        bas_raise(E_ILLEGAL_FUNCTION_CALL);
    if (mode == 0) {
        if (bas_graphics) {
            bas_graphics = 0;
            bas_plat_video(0);
            fb = 0;                   /* the text mode owns the console again */
        }
        return;
    }
    if (!bas_plat_video(1)) {
        bas_graphics = 1;
        if (fb_attach())
            gfx_cls(bg_color);
        text_col = 0;
        text_row = 0;
    } else {
        /* No video hardware: stay on the text console. */
        bas_graphics = 0;
    }
}

void bas_cls(void)
{
    if (bas_graphics) {
        gfx_cls(bg_color);
        text_col = 0;
        text_row = 0;
        gfx_present();
        return;
    }
    /* Text mode: clear the screen through the console. */
    con_puts("\033[2J\033[H");
    con_flush();
}

void bas_color(int fg, int bg)
{
    if (fg >= 0) {
        fg_color = fg & 15;
        if (fg_color == 0)
            fg_color = 15;            /* black ink on a black panel is invisible */
    }
    if (bg >= 0)
        bg_color = bg & 15;
}

void bas_pset(double x, double y, int color, int preset)
{
    int px = (int)x, py = (int)y;

    if (!bas_graphics || !fb_attach())
        return;
    if (px < 0 || py < 0 || px >= fb_w || py >= fb_h)
        return;
    if (preset) {
        uint16_t want = rgb565(bg_color);

        if (fb_get(px, py) == want)
            fb_put(px, py, rgb565(color < 0 ? fg_color : color));
    } else {
        fb_put(px, py, rgb565(color < 0 ? fg_color : color));
    }
    last_pen_x = px;
    last_pen_y = py;
    bas_last_x = px;
    bas_last_y = py;
}

/* style selects the dash pattern; GW-BASIC has 15 of them.  Only the solid
 * line and the two simplest dashes are meaningful at this resolution. */
static int style_on(int index, int style)
{
    static const char *patterns[] = {
        "1111111111111111", "1111111111001111", "1100110011001100",
        "1110111011101110", "1111100011111000", "1111001100110011",
        "1111111000000000", "1101101101101101", "1000100010001000",
        "1100001100001100", "1110000000000000", "1111000000000000",
        "1010101010101010", "1001001001001001", "1000000000000000",
    };
    const char *p;

    if (style <= 0 || style > 15)
        return 1;
    p = patterns[style - 1];
    return p[index % 16] == '1';
}

void bas_line_draw(double x1, double y1, double x2, double y2, int color,
                   int style, int box, int fill)
{
    int ax, ay, bx, by;
    uint16_t c;

    if (!bas_graphics || !fb_attach())
        return;
    c = rgb565(color < 0 ? fg_color : color);
    ax = (int)x1;
    ay = (int)y1;
    bx = (int)x2;
    by = (int)y2;
    if (box) {
        int left = ax < bx ? ax : bx, right = ax > bx ? ax : bx;
        int top = ay < by ? ay : by, bottom = ay > by ? ay : by;
        int x, y;

        if (fill) {
            for (y = top; y <= bottom; y++)
                for (x = left; x <= right; x++)
                    fb_put(x, y, c);
        } else {
            for (x = left; x <= right; x++) {
                fb_put(x, top, c);
                fb_put(x, bottom, c);
            }
            for (y = top; y <= bottom; y++) {
                fb_put(left, y, c);
                fb_put(right, y, c);
            }
        }
    } else {
        /* Bresenham, with the dash pattern applied along the major axis. */
        int dx = bx > ax ? bx - ax : ax - bx;
        int dy = by > ay ? by - ay : ay - by;
        int sx = ax < bx ? 1 : -1, sy = ay < by ? 1 : -1;
        int err = dx - dy;
        int step = 0;

        for (;;) {
            if (style_on(step, style))
                fb_put(ax, ay, c);
            if (ax == bx && ay == by)
                break;
            {
                int e2 = 2 * err;

                if (e2 > -dy) {
                    err -= dy;
                    ax += sx;
                }
                if (e2 < dx) {
                    err += dx;
                    ay += sy;
                }
            }
            step++;
        }
    }
    last_pen_x = bx;
    last_pen_y = by;
    bas_last_x = bx;
    bas_last_y = by;
}

void bas_circle_draw(double x, double y, double r, int color,
                     double start, double end, double aspect)
{
    int cx = (int)x, cy = (int)y;
    uint16_t c = rgb565(color < 0 ? fg_color : color);
    int steps, i;

    if (!bas_graphics || !fb_attach())
        return;
    if (r <= 0)
        return;
    if (aspect <= 0)
        aspect = 1;
    steps = (int)(r * 8) + 32;
    if (steps > 2000)
        steps = 2000;
    for (i = 0; i <= steps; i++) {
        double t = start + (end - start) * (double)i / (double)steps;
        double px = x + r * bas_cos(t);
        double py = y - r * aspect * bas_sin(t);

        fb_put((int)px, (int)py, c);
    }
    (void)cx;
    (void)cy;
}

/* PAINT: a flood fill from (x,y) that stops at border colour. */
#define FILL_STACK 4096

void bas_paint_at(double x, double y, int color, int border)
{
    static int stack[FILL_STACK * 2];
    int        sp = 0;
    uint16_t   fill = rgb565(color < 0 ? fg_color : color);
    uint16_t   edge;

    if (!bas_graphics || !fb_attach())
        return;
    {
        int px = (int)x, py = (int)y;

        if (px < 0 || py < 0 || px >= fb_w || py >= fb_h)
            return;
        edge = (border >= 0) ? rgb565(border) : fb_get(px, py);
        if (edge == fill)
            return;
        stack[sp++] = px;
        stack[sp++] = py;
    }
    while (sp > 0) {
        int py = stack[--sp];
        int px = stack[--sp];
        int left, right, y2;

        if (px < 0 || px >= fb_w || py < 0 || py >= fb_h)
            continue;
        if (fb_get(px, py) != edge)
            continue;
        left = px;
        while (left > 0 && fb_get(left - 1, py) == edge)
            left--;
        right = px;
        while (right < fb_w - 1 && fb_get(right + 1, py) == edge)
            right++;
        for (y2 = left; y2 <= right; y2++)
            fb_put(y2, py, fill);
        for (y2 = left; y2 <= right; y2++) {
            if (py > 0 && fb_get(y2, py - 1) == edge && sp + 2 <= FILL_STACK * 2) {
                stack[sp++] = y2;
                stack[sp++] = py - 1;
            }
            if (py < fb_h - 1 && fb_get(y2, py + 1) == edge && sp + 2 <= FILL_STACK * 2) {
                stack[sp++] = y2;
                stack[sp++] = py + 1;
            }
        }
    }
}

int bas_point(double x, double y)
{
    int px = (int)x, py = (int)y;

    if (!bas_graphics || !fb_attach())
        return -1;
    if (px < 0 || py < 0 || px >= fb_w || py >= fb_h)
        return -1;
    {
        uint16_t v = fb_get(px, py);
        int      i;

        for (i = 0; i < 16; i++)
            if (cga16[i] == v)
                return i;
        return -1;
    }
}

/* SCREEN(row, col[, mode]): read a character cell.  mode 0 asks for the
 * attribute, 1 for the character code.  In graphics mode the panel has no text
 * model, so the answer is the background colour and a space. */
int bas_screen_function(int row, int col, int mode)
{
    if (bas_graphics)
        return mode ? ' ' : (bg_color << 4);
    (void)row;
    (void)col;
    return mode ? ' ' : 0x07;
}
