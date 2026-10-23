#pragma once
#include "ae350_video.h"
#include "vt.h"

/* The BIOS VT100/ANSI state machine is shared; this adapter renders its cells
 * into the AE350 back buffer and presents through the video ownership API. */
struct ae350_tty {
    vt_t terminal;
    vt_cell_t drawn[2][VT_ROWS][VT_COLS];
    unsigned initialized[2],dirty;
};
void ae350_tty_init(struct ae350_tty *tty);
void ae350_tty_putc(struct ae350_tty *tty,unsigned char ch);
void ae350_tty_write(struct ae350_tty *tty,const char *text);
int ae350_tty_flush(struct ae350_tty *tty);
