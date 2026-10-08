#pragma once
#include <hal/hal.h>
#include "font5x7.h"
enum { RGB_WIDTH=480, RGB_HEIGHT=272 };

/* Glyphs come from the shared 5x7 cell font, which the GW-BASIC payload uses
 * as well; only the drawing helpers live here.  A translation unit may include
 * this header and use only some of the helpers (the BIOS console uses cell()
 * alone), so each one carries `unused` to stay warning-free under -Werror. */
static const uint8_t *glyph(char ch) { return font5x7_glyph((unsigned char)ch); }
static void __attribute__((unused))
cell(volatile uint16_t *fb,unsigned x,unsigned y,char ch,unsigned scale,uint16_t fg,uint16_t bg) {
    const uint8_t *bits=glyph(ch);
    for(unsigned dy=0;dy<8*scale;++dy)for(unsigned dx=0;dx<6*scale;++dx)
        fb[(y+dy)*RGB_WIDTH+x+dx]=(dy<7*scale && dx<5*scale && (bits[dy/scale]&(16u>>(dx/scale))))?fg:bg;
}
static void __attribute__((unused))
text(volatile uint16_t *fb,unsigned x,unsigned y,const char *s,unsigned scale,uint16_t fg,uint16_t bg) {
    while(*s && x+6*scale<=RGB_WIDTH) {cell(fb,x,y,*s++,scale,fg,bg);x+=6*scale;}
}
static void __attribute__((unused))
fill(volatile uint16_t *fb,unsigned x,unsigned y,unsigned w,unsigned h,uint16_t color) {
    for(unsigned dy=0;dy<h;++dy) {
        for(unsigned dx=0;dx<w;++dx)fb[(y+dy)*RGB_WIDTH+x+dx]=color;
        if(!(dy&7u))hal_poll();
    }
}
