#include "io.h"
#include <generated/csr.h>
#define PIXELS (480u*272u)
volatile uint16_t *video_frame(unsigned slot) {
    return slot<2 ? (volatile uint16_t *)(0x47e00000u+slot*0x40000u) : 0;
}
int video_stop(void) {
    rgb_lcd_enable_write(0);__asm__ volatile("fence rw,rw" ::: "memory");
    uint32_t start=io_ticks();io_delay_ms(1);
    while(rgb_lcd_busy_read()) if((uint32_t)(io_ticks()-start)>CONFIG_CLOCK_FREQUENCY/10u) return 0;
    return 1;
}
int video_present(unsigned slot) {
    if(slot>1) return 0;
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
    for(unsigned slot=0;slot<2;++slot) for(unsigned i=0;i<PIXELS;++i) video_frame(slot)[i]=0;
    __asm__ volatile("fence rw,rw" ::: "memory");rgb_lcd_select_write(0);rgb_lcd_enable_write(1);
    io_delay_ms(100);unsigned before=rgb_lcd_completed_read();io_delay_ms(40);
    return rgb_lcd_completed_read()!=before && !rgb_lcd_underflows_read();
}
