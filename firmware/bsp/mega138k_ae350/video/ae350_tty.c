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
static uint8_t tty_packed_glyph[28];

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
        /* GFX2D path: per changed cell, fill the 8x16 cell with bg then
         * IMAGE_BLIT the 5x14 glyph (7 font rows x2 vertical) at (1,1).
         * The glyph packs MSB-first, 16 stream pixels per row (2 bytes:
         * 5 glyph bits + padding), matching the engine's per-beat
         * 16-pixel consumption. */
        uint32_t base=(uint32_t)(uintptr_t)fb;
        if(!t->initialized[slot]) {
            ae350_gfx2d_fill_rect(base,s.stride,pitch,s.height,0);
            ae350_gfx2d_wait(0xffff);
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
            uint8_t *packed=tty_packed_glyph;
            for(unsigned r=0;r<14;r++) {
                packed[2*r]=(uint8_t)(glyph[r/2]<<3);
                packed[2*r+1]=0;
            }
            uint32_t cell_px=(yoff+row*16u)*pitch+xoff+col*8u;
            uint32_t cell_dst=base+cell_px*2u;
            ae350_gfx2d_fill_rect(cell_dst,s.stride,8,16,bg);
            ae350_gfx2d_wait(0xffff);
            ae350_gfx2d_image_blit((uint32_t)(uintptr_t)packed,2,
                cell_dst+(pitch+1)*2u,s.stride,5,14,fg,bg);
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
