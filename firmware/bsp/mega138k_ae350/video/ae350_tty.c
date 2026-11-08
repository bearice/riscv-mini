#include "ae350_tty.h"
#include "ae350_gfx2d.h"
#include "font5x7.h"

/* GFX2D accel probe (cached); falls back to the CPU loop when the
 * engine is absent so the TTY still works on older bitstreams. */
static int tty_gfx2d_ready(void) {
    static int cached=-1;
    if(cached<0)cached=ae350_gfx2d_probe()==AE350_GFX2D_OK;
    return cached;
}

/* Glyph pack buffer must live in DDR: the GFX2D engine reads the blit
 * source through its DDR port, so a DLM stack buffer is unreachable. */
static uint8_t tty_packed_glyph[16];

void ae350_tty_init(struct ae350_tty *t) {
    vt_reset(&t->terminal);t->terminal.mode=VT_MODE_CURSOR;
    t->initialized[0]=t->initialized[1]=0;t->dirty=1;
}
void ae350_tty_putc(struct ae350_tty *t,unsigned char ch) {
    vt_putc(&t->terminal,ch);t->dirty=1;
}
void ae350_tty_write(struct ae350_tty *t,const char *text) {
    while(*text)ae350_tty_putc(t,(unsigned char)*text++);
}

int ae350_tty_flush(struct ae350_tty *t) {
    if(!t || !t->dirty)return AE350_VIDEO_OK;
    struct ae350_video_status s;
    int error=ae350_video_status(&s);
    if(error)return error;
    if(s.width<VT_COLS*8u || s.height<VT_ROWS*16u)return AE350_VIDEO_DEVICE;
    unsigned slot=s.enabled?(s.active^1u):0u;
    uint16_t *fb=ae350_video_frame(slot);
    if(!fb)return AE350_VIDEO_BUSY;
    unsigned pitch=s.stride/2u,xoff=(s.width-VT_COLS*8u)/2u,yoff=(s.height-VT_ROWS*16u)/2u;
    if(!t->initialized[slot] && !tty_gfx2d_ready())
        for(unsigned y=0;y<s.height;y++)for(unsigned x=0;x<pitch;x++)fb[y*pitch+x]=0;
    if(tty_gfx2d_ready()) {
        /* GFX2D path: per changed cell, one IMAGE_BLIT of the full 8x16
         * cell (border rows/cols are background, glyph doubled 2x at (1,1)).
         * A single op per cell halves APB/sync overhead vs fill+blit. */
        uint32_t base=(uint32_t)(uintptr_t)fb;
        if(!t->initialized[slot]) {
            ae350_gfx2d_fill_rect(base,s.stride,pitch,s.height,0);
            ae350_gfx2d_wait(0xffff);
        } else {
            /* Scroll detection: if the grid moved up k rows inside the
             * DECSTBM region, COPY_AREA the pixels instead of redrawing
             * every cell (~30x cheaper for one-row scrolls). Compare
             * ignoring the cursor REVERSE bit: drawn[] stores the
             * post-reverse cell, the grid stores the raw cell. Rows the
             * copy does not cover fall back to the normal diff below. */
            unsigned top=t->terminal.top,bottom=t->terminal.bottom;
            unsigned kmax=bottom-top; if(kmax>8)kmax=8;
            for(unsigned k=1;k<=kmax;k++) {
                int match=1;
                for(unsigned r=top;r+k<=bottom&&match;r++)
                    for(unsigned c=0;c<VT_COLS;c++) {
                        vt_cell_t g=t->terminal.grid[r][c];
                        vt_cell_t d=t->drawn[slot][r+k][c];
                        if(g.ch!=d.ch || (g.attr&~ATTR_REVERSE)!=(d.attr&~ATTR_REVERSE)) {match=0;break;}
                    }
                if(!match)continue;
                /* Rows below bottom-k keep stale drawn[] entries: the diff
                 * below redraws any that differ from the grid. */
                uint32_t src_px=(yoff+(top+k)*16u)*pitch+xoff;
                uint32_t dst_px=(yoff+top*16u)*pitch+xoff;
                unsigned hh=(bottom-top+1u-k)*16u;
                ae350_gfx2d_copy_area(base+src_px*2u,s.stride,
                    base+dst_px*2u,s.stride,VT_COLS*8u,hh);
                ae350_gfx2d_wait(0xffff);
                for(unsigned r=top;r+k<=bottom;r++)
                    for(unsigned c=0;c<VT_COLS;c++) t->drawn[slot][r][c]=t->drawn[slot][r+k][c];
                break;
            }
        }
        for(unsigned row=0;row<VT_ROWS;row++)for(unsigned col=0;col<VT_COLS;col++) {
            vt_cell_t cell=t->terminal.grid[row][col];
            if((t->terminal.mode&VT_MODE_CURSOR) && row==t->terminal.cy && col==t->terminal.cx)
                cell.attr^=ATTR_REVERSE;
            if(t->initialized[slot]) {
                vt_cell_t old=t->drawn[slot][row][col];
                if(old.ch==cell.ch && old.attr==cell.attr)continue;
            }
            uint16_t fg,bg;vt_colors(&cell,&fg,&bg);
            const uint8_t *glyph=font5x7_glyph((unsigned char)cell.ch);
            /* Pack the full 8x16 cell in one blit: row 0 and 15 are all
             * background, rows 1..14 are the 5x7 glyph doubled vertically,
             * 5 glyph bits plus 3 background bits per byte. */
            uint8_t *packed=tty_packed_glyph;
            packed[0]=0;packed[15]=0;
            for(unsigned r=1;r<15;r++)packed[r]=(uint8_t)(glyph[(r-1)/2]<<2);
            uint32_t cell_px=(yoff+row*16u)*pitch+xoff+col*8u;
            uint32_t cell_dst=base+cell_px*2u;
            ae350_gfx2d_image_blit((uint32_t)(uintptr_t)packed,1,
                cell_dst,s.stride,8,16,fg,bg);
            ae350_gfx2d_wait(0xffff);
            t->drawn[slot][row][col]=cell;
        }
        t->initialized[slot]=1;error=ae350_video_present(slot);
        if(!error)t->dirty=0;
        return error;
    }
    for(unsigned row=0;row<VT_ROWS;row++)for(unsigned col=0;col<VT_COLS;col++) {
        vt_cell_t cell=t->terminal.grid[row][col];
        if((t->terminal.mode&VT_MODE_CURSOR) && row==t->terminal.cy && col==t->terminal.cx)
            cell.attr^=ATTR_REVERSE;
        if(t->initialized[slot]) {
            vt_cell_t old=t->drawn[slot][row][col];
            if(old.ch==cell.ch && old.attr==cell.attr)continue;
        }
        uint16_t fg,bg;vt_colors(&cell,&fg,&bg);
        const uint8_t *glyph=font5x7_glyph((unsigned char)cell.ch);
        for(unsigned y=0;y<16;y++)for(unsigned x=0;x<8;x++) {
            unsigned on=(y>=1 && y<15 && x>=1 && x<6) && (glyph[(y-1)/2]&(16u>>(x-1)));
            fb[(yoff+row*16u+y)*pitch+xoff+col*8u+x]=on?fg:bg;
        }
        t->drawn[slot][row][col]=cell;
    }
    t->initialized[slot]=1;error=ae350_video_present(slot);
    if(!error)t->dirty=0;
    return error;
}
