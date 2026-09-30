#include "io.h"
#include <generated/csr.h>
#include "lcd_init.h"

#define WIDTH 240u
#define HEIGHT 135u
static uint16_t *const frame=(uint16_t *)0x40300000u;
static unsigned pins=6; /* bit0 DC, bit1 RESET_N, bit2 BL_N (active low). */
static int byte(unsigned dc, unsigned value) {
    pins=(pins&~1u)|dc;
    lcd_gpio_out_write(pins);
    return spi_transfer(1,value,8,0);
}
static int cmd(unsigned value) { return byte(0,value); }
static int data(unsigned value) { return byte(1,value); }
static int window(void) {
    return cmd(0x2a) && data(0) && data(40) && data(1) && data(23)
        && cmd(0x2b) && data(0) && data(53) && data(0) && data(187) && cmd(0x2c);
}
/* Original compact 5x7 row glyphs, bit4 is leftmost. */
static const unsigned char letters[26][7]={
 {14,17,17,31,17,17,17},{30,17,17,30,17,17,30},{14,17,16,16,16,17,14},
 {30,17,17,17,17,17,30},{31,16,16,30,16,16,31},{31,16,16,30,16,16,16},
 {14,17,16,23,17,17,14},{17,17,17,31,17,17,17},{14,4,4,4,4,4,14},
 {7,2,2,2,2,18,12},{17,18,20,24,20,18,17},{16,16,16,16,16,16,31},
 {17,27,21,21,17,17,17},{17,25,21,19,17,17,17},{14,17,17,17,17,17,14},
 {30,17,17,30,16,16,16},{14,17,17,17,21,18,13},{30,17,17,30,20,18,17},
 {15,16,16,14,1,1,30},{31,4,4,4,4,4,4},{17,17,17,17,17,17,14},
 {17,17,17,17,17,10,4},{17,17,17,21,21,21,10},{17,17,10,4,10,17,17},
 {17,17,10,4,4,4,4},{31,1,2,4,8,16,31}};
static const unsigned char digits[10][7]={
 {14,17,19,21,25,17,14},{4,12,4,4,4,4,14},{14,17,1,2,4,8,31},
 {30,1,1,14,1,1,30},{2,6,10,18,31,2,2},{31,16,16,30,1,1,30},
 {14,16,16,30,17,17,14},{31,1,2,4,8,8,8},{14,17,17,14,17,17,14},
 {14,17,17,15,1,1,14}};
static void text(unsigned x, unsigned y, const char *s) {
    while (*s && x+10<WIDTH) {
        unsigned ch=(unsigned char)*s++;
        const unsigned char *glyph=ch>='A' && ch<='Z' ? letters[ch-'A'] :
            ch>='0' && ch<='9' ? digits[ch-'0'] : 0;
        if (glyph) for (unsigned row=0; row<7; ++row) for (unsigned col=0; col<5; ++col)
            if (glyph[row]&(16u>>col)) for (unsigned a=0; a<2; ++a) for (unsigned b=0; b<2; ++b)
                frame[(y+2*row+a)*WIDTH+x+2*col+b]=0xffff;
        x+=12;
    }
}
int lcd_show(unsigned sd_ready) {
    lcd_spi_clk_divider_write((CONFIG_CLOCK_FREQUENCY+5999999u)/6000000u); /* 6 MHz, mode 0, MSB first. */
    spi_select(1,0); pins=4; lcd_gpio_out_write(pins); io_delay_ms(100);
    pins=6; lcd_gpio_out_write(pins); io_delay_ms(200);
    spi_select(1,1);
    if (!cmd(0x11)) goto fail;
    io_delay_ms(120);
    for (unsigned i=0; i<sizeof(lcd_init)/sizeof(lcd_init[0]); ++i)
        if (!byte(lcd_init[i]>>8,lcd_init[i]&255)) goto fail;
    for (unsigned y=0; y<HEIGHT; ++y) for (unsigned x=0; x<WIDTH; ++x) {
        uint16_t color=0;
        if (!x || x==WIDTH-1 || !y || y==HEIGHT-1) color=0xffff;
        frame[y*WIDTH+x]=color;
    }
    text(60,8,"RISCV MINI"); text(48,26,"DDR 128 MB");
    text(36,110,sd_ready ? "SD CARD READY" : "SD NOT READY");
    if (!window()) goto fail;
    pins|=1; lcd_gpio_out_write(pins);
    for (unsigned i=0; i<WIDTH*HEIGHT; ++i)
        if (!spi_transfer(1,frame[i],16,0)) goto fail;
    spi_select(1,0); pins&=~4u; lcd_gpio_out_write(pins);
    return 1;
fail:
    spi_select(1,0); puts_uart("LCD transfer failed\r\n"); return 0;
}
