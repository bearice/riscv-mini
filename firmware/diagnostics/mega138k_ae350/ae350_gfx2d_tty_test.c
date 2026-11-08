/* GFX2D TTY board test: ae350_tty_flush via IMAGE_BLIT vs CPU expectation.
 *
 * Writes result words at AE350_RESULTS_BASE; slot0 == 0xC3200000 means
 * all checks passed. Slot layout:
 *   [0]=stage [1]=video_init rc [2]=flush1 rc [3]=bad pixels 'A'@(0,0)
 *   [4]=flush2 rc [5]=bad pixels 'B'@(0,1) [6]=COMMANDS [7]=ERRORS
 */
#include <stdint.h>
#include "ae350_address_map.h"
#include "ae350_tty.h"
#include "ae350_dma.h"
#include "ae350_gfx2d.h"
#include "vt.h"
#include "font5x7.h"

static struct ae350_tty tty;
static volatile uint32_t *const out = (volatile uint32_t *)AE350_RESULTS_BASE;

/* Strong override: record trap details where the reader can see them. */
__attribute__((noreturn)) void trap_report(uint32_t cause, uint32_t pc,
                                           uint32_t value, uint32_t ra,
                                           uint32_t sp) {
    out[8] = 0x80000000u | cause;
    out[9] = pc;
    out[10] = value;
    out[11] = ra;
    out[12] = sp;
    for (;;) __asm__ volatile("wfi");
}

static void check_cell(uint16_t *fb, unsigned pitch, unsigned xoff,
                       unsigned yoff, unsigned col, unsigned row,
                       vt_cell_t cell, unsigned *bad) {
    uint16_t fg, bg;
    vt_colors(&cell, &fg, &bg);
    const uint8_t *glyph = font5x7_glyph((unsigned char)cell.ch);
    unsigned b = 0;
    for (unsigned y = 0; y < 16; y++)
        for (unsigned x = 0; x < 8; x++) {
            unsigned on = (y >= 1 && y < 15 && x >= 1 && x < 6) &&
                          (glyph[(y - 1) / 2] & (16u >> (x - 1)));
            uint16_t want = on ? fg : bg;
            uint16_t got = fb[(yoff + row * 16 + y) * pitch + xoff + col * 8 + x];
            if (got != want) b++;
        }
    if (bad) *bad = b;
}

int main(void) {
    /* Match BIOS (services.c): invalidate then enable I+D caches. The
     * bootloader leaves caches off; without I-cache, tight GFX2D polling
     * starves fetch and faults. */
    __asm__ volatile("csrw 0x7cc,%0\ncsrs 0x7ca,%1" :: "r"(23), "r"(3) : "memory");
    for (unsigned i = 0; i < 8; i++) out[i] = 0;
    out[0] = 0xC3200000u;

    out[1] = ae350_video_init((uint16_t *)AE350_DIAG_VIDEO_FB0,
                              (uint16_t *)AE350_DIAG_VIDEO_FB1, 1280);
    out[0] = 0xC3200010u;
    if (out[1]) { out[0] = 0xC3200001u; return 0; }

    struct ae350_video_status s;
    ae350_video_status(&s);
    out[0] = 0xC3200011u;
    unsigned pitch = s.stride / 2u;
    unsigned xoff = (s.width - VT_COLS * 8u) / 2u;
    unsigned yoff = (s.height - VT_ROWS * 16u) / 2u;
    unsigned slot = s.enabled ? (s.active ^ 1u) : 0u;
    uint16_t *fb = ae350_video_frame(slot);
    out[0] = 0xC3200012u;
    if (!fb) { out[0] = 0xC3200001u; return 0; }

    ae350_tty_init(&tty);
    out[0] = 0xC3200013u;
    ae350_tty_write(&tty, "A");
    out[0] = 0xC3200014u;
    out[2] = ae350_tty_flush(&tty);
    out[0] = 0xC3200020u;
    /* fb was written by GFX2D DMA; invalidate D-cache lines before CPU reads. */
    ae350_dma_sync_for_cpu((uint32_t)(uintptr_t)fb, 1280u * 480u);
    check_cell(fb, pitch, xoff, yoff, 0, 0, tty.terminal.grid[0][0],
               (unsigned *)&out[3]);

    ae350_tty_write(&tty, "B");
    out[4] = ae350_tty_flush(&tty);
    out[0] = 0xC3200030u;
    /* present() flips the slot: re-fetch the just-drawn buffer. */
    ae350_video_status(&s);
    fb = (uint16_t *)(s.active ? AE350_DIAG_VIDEO_FB1 : AE350_DIAG_VIDEO_FB0);
    if (!fb) { out[0] = 0xC3200001u; return 0; }
    ae350_dma_sync_for_cpu((uint32_t)(uintptr_t)fb, 1280u * 480u);
    check_cell(fb, pitch, xoff, yoff, 1, 0, tty.terminal.grid[0][1],
               (unsigned *)&out[5]);

    out[6] = *(volatile uint32_t *)(AE350_GFX2D_BASE + AE350_GFX2D_COMMANDS);
    out[7] = *(volatile uint32_t *)(AE350_GFX2D_BASE + AE350_GFX2D_ERRORS);

    /* Scroll phase: build a near-full frame, flush twice (alternating
     * slots), then scroll and flush on the slot whose drawn[] holds the
     * near-full frame: the COPY_AREA scroll path must reproduce the
     * grid on screen. */
    for (unsigned n = 0; n < 25; n++) {
        char line[4] = { (char)('A' + (n % 26)), (char)('0' + (n / 10)), (char)('0' + (n % 10)), 0 };
        ae350_tty_write(&tty, line);
        ae350_tty_write(&tty, "\r\n");
    }
    out[6] = ae350_tty_flush(&tty);
    ae350_tty_write(&tty, "X");
    out[6] = ae350_tty_flush(&tty);
    { volatile uint32_t *gc = (volatile uint32_t *)(AE350_GFX2D_BASE + AE350_GFX2D_COMMANDS);
      out[7] = *gc; }
    for (unsigned n = 25; n < 35; n++) {
        char line[4] = { (char)('A' + (n % 26)), (char)('0' + (n / 10)), (char)('0' + (n % 10)), 0 };
        ae350_tty_write(&tty, line);
        ae350_tty_write(&tty, "\r\n");
    }
    out[6] = ae350_tty_flush(&tty);
    out[0] = 0xC3200040u;
    ae350_video_status(&s);
    fb = (uint16_t *)(s.active ? AE350_DIAG_VIDEO_FB1 : AE350_DIAG_VIDEO_FB0);
    if (!fb) { out[0] = 0xC3200001u; return 0; }
    ae350_dma_sync_for_cpu((uint32_t)(uintptr_t)fb, 1280u * 480u);
    check_cell(fb, pitch, xoff, yoff, 0, 0, tty.terminal.grid[0][0],
               (unsigned *)&out[8]);
    { vt_cell_t cur = tty.terminal.grid[29][0]; cur.attr ^= ATTR_REVERSE;
      check_cell(fb, pitch, xoff, yoff, 0, 29, cur, (unsigned *)&out[9]); }
    check_cell(fb, pitch, xoff, yoff, 40, 15, tty.terminal.grid[15][40],
               (unsigned *)&out[10]);
    check_cell(fb, pitch, xoff, yoff, 1, 0, tty.terminal.grid[0][1],
               (unsigned *)&out[11]);
    /* 35 lines + trailing CRLF scrolls 6 times: row 0 shows line 6 ('G'),
     * row 29 is the blank cursor row. */
    out[12] = (unsigned)(tty.terminal.grid[0][0].ch == 'G' &&
                         tty.terminal.grid[29][0].ch == ' ');
    out[13] = *(volatile uint32_t *)(AE350_GFX2D_BASE + AE350_GFX2D_ERRORS);
    ae350_video_status(&s);
    out[14] = s.active;  /* buffer the scroll checks read */
    out[15] = (unsigned)tty.terminal.grid[15][40].attr;

    if (out[2] || out[3] || out[4] || out[5] || out[8] || out[9] ||
        out[10] || out[11] || out[13] || !out[12])
        out[0] = 0xC3200002u;
    __asm__ volatile("fence" ::: "memory");
    for (;;) __asm__ volatile("wfi");
}
