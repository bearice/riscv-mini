#include "ae350_tty.h"
#include "font5x7.h"

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
    if(!t->initialized[slot])
        for(unsigned y=0;y<s.height;y++)for(unsigned x=0;x<pitch;x++)fb[y*pitch+x]=0;
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
