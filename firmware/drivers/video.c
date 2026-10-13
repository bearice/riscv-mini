#include "io.h"
#include <generated/csr.h>
#define PIXELS (480u*272u)
/* Linker-owned DDR buffers; hardware has no fixed VRAM addresses. */
static uint16_t frames[2][PIXELS] __attribute__((aligned(16)));
volatile uint16_t *video_frame(unsigned slot) {
    return slot<2 ? (volatile uint16_t *)(uintptr_t)(slot?rgb_lcd_base1_read():rgb_lcd_base0_read()) : 0;
}
int video_set_buffers(uint16_t *first,uint16_t *second) {
    uintptr_t a=(uintptr_t)first,b=(uintptr_t)second;
    unsigned bytes=sizeof(frames[0]) >> rgb_lcd_format_read();
    if(rgb_lcd_enable_read() || rgb_lcd_busy_read())return 0;
    if(a<4096 || b<4096 || (a&15u) || (b&15u) || a>MINI_RAM_END-bytes || b>MINI_RAM_END-bytes)return 0;
    if(a<b+bytes && b<a+bytes)return 0;
    rgb_lcd_base0_write(a);rgb_lcd_base1_write(b);return 1;
}
int video_stop(void) {
    rgb_lcd_enable_write(0);__asm__ volatile("fence rw,rw" ::: "memory");
    uint32_t start=io_ticks();io_delay_ms(1);
    while(rgb_lcd_busy_read()) if((uint32_t)(io_ticks()-start)>CONFIG_CLOCK_FREQUENCY/10u) return 0;
    return 1;
}
int video_present(unsigned slot) {
    if(slot>1 || rgb_lcd_format_read()) return 0;
    __asm__ volatile("fence rw,rw" ::: "memory");rgb_lcd_select_write(slot);
    uint32_t start=io_ticks();
    while(rgb_lcd_active_read()!=slot) if((uint32_t)(io_ticks()-start)>CONFIG_CLOCK_FREQUENCY/10u) return 0;
    return 1;
}
void video_status(void) {
    puts_uart("RGB LCD active=");io_hex(rgb_lcd_active_read());
    puts_uart(" frames=");io_hex(rgb_lcd_frames_read());
    puts_uart(" completed=");io_hex(rgb_lcd_completed_read());
    puts_uart(" underflows=");io_hex(rgb_lcd_underflows_read());puts_uart("\r\n");
}
int video_init(void) {
    if(!video_stop()) return 0;
    rgb_lcd_format_write(0);rgb_lcd_window_start_write(0);
    rgb_lcd_wrap_start_write(0);rgb_lcd_wrap_end_write(0);
    if(!video_set_buffers(frames[0],frames[1]))return 0;
    for(unsigned slot=0;slot<2;++slot) for(unsigned i=0;i<PIXELS;++i) video_frame(slot)[i]=0;
    __asm__ volatile("fence rw,rw" ::: "memory");rgb_lcd_select_write(0);rgb_lcd_enable_write(1);
    io_delay_ms(100);unsigned before=rgb_lcd_completed_read();io_delay_ms(40);
    return rgb_lcd_completed_read()!=before && !rgb_lcd_underflows_read();
}

/* Format changes drain the old frame before changing its byte interpretation.
 * INDEX8 reuses one half of the RGB compatibility pool for two compact slots. */
unsigned video_format(void) {return rgb_lcd_format_read()?8u:565u;}
int video_configure(unsigned indexed) {
    if(indexed>1 || !video_stop())return 0;
    rgb_lcd_format_write(indexed);
    rgb_lcd_wrap_start_write(0);rgb_lcd_wrap_end_write(0);
    rgb_lcd_window_start_write(0);rgb_lcd_window_end_write(0);
    if(!video_set_buffers(frames[0],indexed?frames[0]+PIXELS/2:frames[1]))return 0;
    if(indexed)for(unsigned i=0;i<512;++i) {
        rgb_lcd_palette_address_write(i);rgb_lcd_palette_data_write(0);
    }
    unsigned bytes=sizeof(frames[0])>>indexed;
    for(unsigned slot=0;slot<2;++slot) {
        volatile uint16_t *fb=video_frame(slot);
        for(unsigned i=0;i<bytes/2;++i)fb[i]=0;
    }
    rgb_lcd_commit_write(0);rgb_lcd_select_write(0);
    __asm__ volatile("fence rw,rw" ::: "memory");
    io_delay_ms(1);rgb_lcd_enable_write(1);return 1;
}
int video_palette(uint16_t *table,unsigned bank,unsigned count,unsigned readback) {
    if(!table || bank>1 || count!=256 || !rgb_lcd_format_read())return 0;
    if(!readback && rgb_lcd_enable_read() && bank==rgb_lcd_palette_active_read())return 0;
    for(unsigned i=0;i<count;++i) {
        rgb_lcd_palette_address_write(bank*256+i);
        if(readback)table[i]=rgb_lcd_palette_readback_read();
        else rgb_lcd_palette_data_write(table[i]);
    }
    return 1;
}
int video_present_indexed(unsigned slot,unsigned bank) {
    if(slot>1 || bank>1 || !rgb_lcd_format_read())return 0;
    __asm__ volatile("fence rw,rw" ::: "memory");
    rgb_lcd_commit_write((bank<<1)|slot);
    uint32_t start=io_ticks();
    while(rgb_lcd_active_read()!=slot || rgb_lcd_palette_active_read()!=bank)
        if((uint32_t)(io_ticks()-start)>CONFIG_CLOCK_FREQUENCY/10u)return 0;
    return 1;
}
int video_window(unsigned x0,unsigned y0,unsigned x1,unsigned y1) {
    if(x0>=x1 || y0>=y1 || x1>480 || y1>272 || !rgb_lcd_format_read())return 0;
    if(!video_stop())return 0;
    rgb_lcd_window_end_write((x1+45)|((y1+14)<<10));
    rgb_lcd_window_start_write((x0+45)|((y0+14)<<10)|(1u<<19));
    io_delay_ms(1);rgb_lcd_enable_write(1);return 1;
}
