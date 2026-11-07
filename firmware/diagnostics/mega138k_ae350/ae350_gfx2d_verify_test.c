/* GFX2D render verify: full-pattern GFX2D render, per-cell pixel compare. */
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
    out[8] = 0x80000000u | cause; out[9] = pc; out[10] = value;
    out[11] = ra; out[12] = sp;
    for (;;) {}
}

int main(void) {
    __asm__ volatile("csrw 0x7cc,%0\ncsrs 0x7ca,%1" :: "r"(23), "r"(3) : "memory");
    for (unsigned i = 0; i < 16; i++) out[i] = 0;
    out[0] = 0xC3500000u;
    if (ae350_video_init((uint16_t *)AE350_DIAG_VIDEO_FB0,
                         (uint16_t *)AE350_DIAG_VIDEO_FB1, 1280)) {
        out[0] = 0xC3500001u; return 0;
    }
    ae350_tty_init(&tty);
    for (unsigned i = 0; i < VT_ROWS * VT_COLS; i++)
        tty.terminal.grid[i / VT_COLS][i % VT_COLS].ch = (uint8_t)('A' + (i % 26));

    uint16_t *fb = (uint16_t *)AE350_DIAG_VIDEO_FB0;
    unsigned pitch = 640;
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
    ae350_dma_sync_for_cpu(AE350_DIAG_VIDEO_FB0, 1280u * 480u);
    unsigned total_bad = 0;
    int first_row = -1, first_col = -1;
    for (unsigned row = 0; row < VT_ROWS; row++)
        for (unsigned col = 0; col < VT_COLS; col++) {
            vt_cell_t cell = tty.terminal.grid[row][col];
            if ((tty.terminal.mode & VT_MODE_CURSOR) && row == tty.terminal.cy && col == tty.terminal.cx)
                cell.attr ^= ATTR_REVERSE;
            uint16_t fg, bg; vt_colors(&cell, &fg, &bg);
            const uint8_t *glyph = font5x7_glyph(cell.ch);
            unsigned bad = 0;
            for (unsigned y = 0; y < 16; y++)
                for (unsigned x = 0; x < 8; x++) {
                    unsigned on = (y >= 1 && y < 15 && x >= 1 && x < 6) &&
                                  (glyph[(y - 1) / 2] & (16u >> (x - 1)));
                    uint16_t want = on ? fg : bg;
                    uint16_t got = fb[(row * 16 + y) * pitch + col * 8 + x];
                    if (got != want) bad++;
                }
            if (bad) {
                if (first_row < 0) { first_row = row; first_col = col; }
                total_bad += bad;
            }
        }
    out[1] = total_bad;
    out[2] = (uint32_t)(first_row < 0 ? -1 : (first_row * 1000 + first_col));
    out[5] = *(volatile uint32_t *)(AE350_GFX2D_BASE + AE350_GFX2D_COMMANDS);
    out[6] = *(volatile uint32_t *)(AE350_GFX2D_BASE + AE350_GFX2D_ERRORS);
    out[0] = 0xC3500010u;
    __asm__ volatile("fence" ::: "memory");
    for (;;) __asm__ volatile("wfi");
}
