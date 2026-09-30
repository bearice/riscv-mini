#include "io.h"
#include <string.h>
#include <generated/csr.h>
#define WIDTH 480u
#define HEIGHT 272u
static unsigned current;
static unsigned expected[2];
static volatile uint16_t *const frames[2]={(uint16_t *)0x47e00000u,(uint16_t *)0x47e40000u};
static void draw(unsigned slot) {
    volatile uint16_t *p=frames[slot];
    unsigned sum=0;
    for (unsigned y=0;y<HEIGHT;++y) for(unsigned x=0;x<WIDTH;++x) {
        uint16_t color=x<160 ? 0xf800 : x<320 ? 0x07e0 : 0x001f;
        if (y<48 || y>=224) color=0;
        if (y>=176 && y<224) color=(uint16_t)(((x*31/479)<<11)|((x*63/479)<<5)|(x*31/479));
        if (!x || x==WIDTH-1 || !y || y==HEIGHT-1) color=0xffff;
        if ((x>=8 && x<24 && y>=8 && y<24) || (x>=456 && x<472 && y>=248 && y<264)) color=slot ? 0xffe0 : 0x07ff;
        p[y*WIDTH+x]=color;
        sum+=color;
    }
    __asm__ volatile("fence rw,rw" ::: "memory");
    expected[slot]=sum;
}
int video_stop(void) {
    rgb_lcd_enable_write(0);
    __asm__ volatile("fence rw,rw" ::: "memory");
    io_delay_ms(1);
    unsigned start=io_ticks();
    while (rgb_lcd_busy_read()) {
        if ((uint32_t)(io_ticks()-start)>(CONFIG_CLOCK_FREQUENCY/10u)) { puts_uart("RGB LCD STOP FAIL: DMA drain timeout\r\n");return 0; }
    }
    return 1;
}
void video_status(void) {
    puts_uart("RGB LCD: active=");io_hex(rgb_lcd_active_read());
    puts_uart(" completed=");io_hex(rgb_lcd_completed_read());
    puts_uart(" checksum=");io_hex(rgb_lcd_checksum_read());
    puts_uart(" busy=");io_hex(rgb_lcd_busy_read());
    puts_uart(" frames=");io_hex(rgb_lcd_frames_read());
    puts_uart(" underflows=");io_hex(rgb_lcd_underflows_read());puts_uart("\r\n");
}
int video_init(void) {
    rgb_lcd_enable_write(0);
    rgb_lcd_test_write(0);
    draw(0);draw(1);current=0;rgb_lcd_select_write(0);
    rgb_lcd_enable_write(1);io_delay_ms(100);
    unsigned before=rgb_lcd_completed_read(), errors=rgb_lcd_underflows_read();io_delay_ms(100);
    if (rgb_lcd_completed_read()==before) { puts_uart("RGB LCD FAIL: DMA frame did not complete\r\n");return 0; }
    video_status();
    if (rgb_lcd_checksum_read()!=expected[0]) { puts_uart("RGB LCD FAIL: frame checksum mismatch\r\n");return 0; }
    if (!rgb_lcd_enable_read() || rgb_lcd_underflows_read()!=errors) { puts_uart("RGB LCD FAIL: DMA underrun\r\n");return 0; }
    puts_uart("RGB LCD DMA PASS: stable scan, no new underflows\r\n");
    puts_uart("M3 READY: 480x272 RGB565 DDR framebuffer, LCD 9 MHz, HDMI disabled\r\n");
    return 1;
}
int video_command(const char *line) {
#ifdef MINI_STRESS
    if (!strcmp(line,"fboff")) { if (video_stop()) puts_uart("LCD DMA stopped\r\n");return 1; }
    if (!strcmp(line,"fbon")) { rgb_lcd_enable_write(1);puts_uart("LCD DMA enabled\r\n");return 1; }
    if (!strcmp(line,"memcopy")) {
        volatile unsigned *src=(unsigned *)0x40400000u, *dst=(unsigned *)0x40408000u;
        unsigned start=io_ticks();
        for (unsigned i=0;i<8192;++i) src[i]=start^(i*0x9e3779b9u);
        for (unsigned i=0;i<8192;++i) { unsigned got=src[i]; if (got!=(start^(i*0x9e3779b9u))) {
            puts_uart("MEM SOURCE FAIL: offset=");io_hex(i*4);
            puts_uart(" expected=");io_hex(start^(i*0x9e3779b9u));
            puts_uart(" got=");io_hex(got);puts_uart("\r\n");return 1;
        } }
        for (unsigned i=0;i<8192;++i) dst[i]=src[i];
        for (unsigned i=0;i<8192;++i) { unsigned got=dst[i]; if (got!=(start^(i*0x9e3779b9u))) {
            puts_uart("MEM COPY FAIL: offset=");io_hex(i*4);
            puts_uart(" expected=");io_hex(start^(i*0x9e3779b9u));
            puts_uart(" got=");io_hex(got);
            puts_uart(" source=");io_hex(src[i]);puts_uart("\r\n");return 1;
        } }
        puts_uart("MEM COPY PASS: bytes=32768 ticks=");io_hex(io_ticks()-start);puts_uart("\r\n");return 1;
    }
#endif
    if (!strcmp(line,"fbcheck")) { io_delay_ms(40);puts_uart(rgb_lcd_checksum_read()==expected[current] ? "FB CHECK PASS\r\n" : "FB CHECK FAIL\r\n");video_status();return 1; }
    if (!strcmp(line,"fbpattern")) { rgb_lcd_test_write(1);puts_uart("LCD direct pattern enabled\r\n");return 1; }
    if (!strcmp(line,"fbmemory")) { rgb_lcd_test_write(0);puts_uart("LCD DDR framebuffer enabled\r\n");return 1; }
    if (!strcmp(line,"fbinfo")) { video_status();return 1; }
    if (!strcmp(line,"fbflip")) {
        unsigned next=current^1u;
        rgb_lcd_select_write(next);
        unsigned start=io_ticks();
        while(rgb_lcd_active_read()!=next) {
            if ((uint32_t)(io_ticks()-start)>(CONFIG_CLOCK_FREQUENCY/10u)) { puts_uart("RGB LCD FLIP FAIL: timeout\r\n");return 1; }
        }
        current=next;
        puts_uart("RGB LCD FLIP PASS: active=");io_hex(current);puts_uart("\r\n");
        video_status();return 1;
    }
    return 0;
}
