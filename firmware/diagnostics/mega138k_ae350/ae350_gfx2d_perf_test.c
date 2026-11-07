/* GFX2D TTY performance: measure CPU loop vs GFX2D per-cell draw.
 *
 * Writes result words at AE350_RESULTS_BASE:
 *   [0]=stage [1]=cpu cycles [2]=gfx2d cycles [3]=cpu pixel verify
 *   [4]=gfx2d pixel verify [5]=COMMANDS [6]=ERRORS
 */
#include <stdint.h>
#include "ae350_address_map.h"
#include "ae350_tty.h"
#include "ae350_dma.h"
#include "ae350_gfx2d.h"
#include "vt.h"
#include "font5x7.h"

static volatile uint32_t *out = (volatile uint32_t *)(AE350_RESULTS_BASE);
static struct ae350_tty tty;

__attribute__((used)) void trap_report(unsigned cause, unsigned pc,
                                        unsigned value, unsigned ra, unsigned sp) {
    out[8] = 0x80000000u | cause;
    out[9] = pc;
    out[10] = value;
    out[11] = ra;
    out[12] = sp;
    for (;;) {}
}

static uint64_t rd_cycle(void) { uint64_t v; __asm__ volatile("csrr %0, 0xc00" : "=r"(v)); return v; }

static void clear_out(void) { for (unsigned i = 0; i < 16; i++) out[i] = 0; }

int main(void) {
    __asm__ volatile("csrw 0x7cc,%0\ncsrs 0x7ca,%1" :: "r"(23), "r"(3) : "memory");
    clear_out();
    out[0] = 0xC3400000u;

    if (ae350_video_init((uint16_t *)AE350_DIAG_VIDEO_FB0,
                         (uint16_t *)AE350_DIAG_VIDEO_FB1, 1280)) {
        out[0] = 0xC3400001u; return 0;
    }
    ae350_tty_init(&tty);
    /* Fill grid with a pattern so every cell differs. */
    for (unsigned i = 0; i < VT_ROWS * VT_COLS; i++)
        tty.terminal.grid[i / VT_COLS][i % VT_COLS].ch = (uint8_t)('A' + (i % 26));

    /* --- CPU path: ae350_tty_flush with GFX2D probe failing is not
     * available (probe succeeds on this bitstream), so time the flush
     * twice: once via the GFX2D path and once via the CPU loop
     * replicated here. --- */
    uint16_t *fb = (uint16_t *)AE350_DIAG_VIDEO_FB0;
    unsigned pitch = 640;

    /* CPU draw (manual per-pixel loop, same geometry as tty.c CPU path). */
    uint64_t t0 = rd_cycle();
    for (unsigned y = 0; y < 480; y++)
        for (unsigned x = 0; x < pitch; x++) fb[y * pitch + x] = 0;
    for (unsigned row = 0; row < VT_ROWS; row++)
        for (unsigned col = 0; col < VT_COLS; col++) {
            vt_cell_t cell = tty.terminal.grid[row][col];
            if ((tty.terminal.mode & VT_MODE_CURSOR) && row == tty.terminal.cy && col == tty.terminal.cx)
                cell.attr ^= ATTR_REVERSE;
            uint16_t fg, bg; vt_colors(&cell, &fg, &bg);
            const uint8_t *glyph = font5x7_glyph(cell.ch);
            for (unsigned gy = 0; gy < 16; gy++)
                for (unsigned gx = 0; gx < 8; gx++) {
                    unsigned on = (gy >= 1 && gy < 15 && gx >= 1 && gx < 6) &&
                                  (glyph[(gy - 1) / 2] & (16u >> (gx - 1)));
                    fb[(row * 16 + gy) * pitch + col * 8 + gx] = on ? fg : bg;
                }
        }
    uint64_t t1 = rd_cycle();
    out[1] = (uint32_t)(t1 - t0);
    /* Verify CPU result matches GFX2D result later. */
    ae350_dma_sync_for_cpu(AE350_DIAG_VIDEO_FB0, 1280u * 480u);
    { /* verify CPU render */
        unsigned bad = 0;
        for (unsigned row = 0; row < VT_ROWS; row++)
            for (unsigned col = 0; col < VT_COLS; col++) {
                vt_cell_t cell = tty.terminal.grid[row][col];
                if ((tty.terminal.mode & VT_MODE_CURSOR) && row == tty.terminal.cy && col == tty.terminal.cx)
                    cell.attr ^= ATTR_REVERSE;
                uint16_t fg, bg; vt_colors(&cell, &fg, &bg);
                const uint8_t *glyph = font5x7_glyph(cell.ch);
                for (unsigned y = 0; y < 16; y++)
                    for (unsigned x = 0; x < 8; x++) {
                        unsigned on = (y >= 1 && y < 15 && x >= 1 && x < 6) &&
                                      (glyph[(y - 1) / 2] & (16u >> (x - 1)));
                        if (fb[(row * 16 + y) * pitch + col * 8 + x] != (on ? fg : bg)) bad++;
                    }
            }
        out[9] = bad;
    }
    uint32_t cpu_sum = 0;
    for (unsigned i = 0; i < 640 * 480; i += 97) cpu_sum += fb[i];
    out[3] = cpu_sum;

    /* GFX2D path: same work via engine (screen fill + per-cell single
     * 8x16 blit, mirroring ae350_tty_flush). */
    t0 = rd_cycle();
    ae350_gfx2d_fill_rect(AE350_DIAG_VIDEO_FB0, 1280, 640, 480, 0);
    ae350_gfx2d_wait(0xffff);
    for (unsigned row = 0; row < VT_ROWS; row++)
        for (unsigned col = 0; col < VT_COLS; col++) {
            vt_cell_t cell = tty.terminal.grid[row][col];
            if ((tty.terminal.mode & VT_MODE_CURSOR) && row == tty.terminal.cy && col == tty.terminal.cx)
                cell.attr ^= ATTR_REVERSE;
            uint16_t fg, bg; vt_colors(&cell, &fg, &bg);
            const uint8_t *glyph = font5x7_glyph(cell.ch);
            static uint8_t packed[16];
            packed[0]=0;packed[15]=0;
            for(unsigned r=1;r<15;r++) packed[r]=(uint8_t)(glyph[(r-1)/2]<<2);
            uint32_t cell_dst = AE350_DIAG_VIDEO_FB0 + ((row * 16u) * pitch + col * 8u) * 2u;
            ae350_gfx2d_image_blit((uint32_t)(uintptr_t)packed, 1,
                cell_dst, 1280, 8, 16, fg, bg);
            ae350_gfx2d_wait(0xffff);
        }
    t1 = rd_cycle();
    out[2] = (uint32_t)(t1 - t0);
    ae350_dma_sync_for_cpu(AE350_DIAG_VIDEO_FB0, 1280u * 480u);
    { uint32_t g_sum = 0;
    for (unsigned i = 0; i < 640 * 480; i += 97) g_sum += fb[i];
    out[4] = g_sum; }
    out[5] = *(volatile uint32_t *)(AE350_GFX2D_BASE + AE350_GFX2D_COMMANDS);
    out[6] = *(volatile uint32_t *)(AE350_GFX2D_BASE + AE350_GFX2D_ERRORS);
    out[0] = 0xC3400010u;
    __asm__ volatile("fence" ::: "memory");
    for (;;) __asm__ volatile("wfi");
}
