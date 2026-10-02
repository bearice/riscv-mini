#include <hal/hal.h>
#include <generated/csr.h>
#include <generated/soc.h>
#include "io.h"
extern volatile hal_stats_t hal_stats;
extern void hal_irq_runtime_init(void);
extern void hal_irq_runtime_start(void);
static uint8_t rx[128];static volatile unsigned head,tail;
static volatile unsigned presses,releases;
static void uart_irq(void *unused) {
    (void)unused;++hal_stats.uart_irqs;
    while(!uart_rxempty_read()) {
        unsigned ch=uart_rxtx_read();uart_ev_pending_write(2);
        if(head-tail<sizeof(rx)) {rx[head&127]=ch;++head;} else ++hal_stats.uart_drops;
    }
}
static void timer_irq(void *unused) {(void)unused;timer1_ev_pending_write(1);++hal_stats.timer_irqs;}
#if MINI_FEATURE_BOARD_IO
static void button_irq(void *unused) {
    (void)unused;
    unsigned p=board_io_pressed_read(),r=board_io_released_read();
    presses|=p;releases|=r;board_io_clear_write(p|(r<<4));
    if(p|r)++hal_stats.button_irqs;
}
#endif
void hal_init(void) {
    hal_irq_runtime_init();io_timer_init();
    uart_ev_enable_write(0);timer1_ev_enable_write(0);
#if MINI_FEATURE_BOARD_IO
    board_io_ev_enable_write(0);
    board_io_leds_write(0);board_io_clear_write(255);
#endif
    hal_irq_attach(UART_INTERRUPT,uart_irq,0);hal_irq_enable(UART_INTERRUPT,1);
    hal_irq_attach(TIMER1_INTERRUPT,timer_irq,0);hal_irq_enable(TIMER1_INTERRUPT,1);
#if MINI_FEATURE_BOARD_IO
    hal_irq_attach(BOARD_IO_INTERRUPT,button_irq,0);hal_irq_enable(BOARD_IO_INTERRUPT,1);
#endif
    timer1_en_write(0);timer1_load_write(CONFIG_CLOCK_FREQUENCY/1000u-1);
    timer1_reload_write(CONFIG_CLOCK_FREQUENCY/1000u-1);timer1_ev_pending_write(1);timer1_ev_enable_write(1);timer1_en_write(1);
#if MINI_FEATURE_BOARD_IO
    board_io_ev_enable_write(1);
#endif
    uart_ev_enable_write(2);hal_irq_runtime_start();
    hal_phys_reset(10);while(hal_ws2812_busy()) {} hal_ws2812_set(0,0,0);
    hal_audio_stop();hal_audio_mute(1);hal_mic_stop();
}
void hal_poll(void) {hal_eth_poll();hal_usb_poll();}
uint32_t hal_time_ms(void) {
    unsigned state=hal_irq_save();timer0_uptime_latch_write(1);uint64_t ticks=timer0_uptime_cycles_read();hal_irq_restore(state);
    return (uint32_t)(ticks/(CONFIG_CLOCK_FREQUENCY/1000u));
}
uint32_t hal_ticks(void) {return io_ticks();}
void hal_delay_ms(unsigned ms) {
    uint32_t start=hal_time_ms();while((uint32_t)(hal_time_ms()-start)<ms)hal_poll();
}
int hal_uart_getc(void) {
    unsigned state=hal_irq_save();int value=-1;
    if(head!=tail) {value=rx[(tail++)&127];}
    hal_irq_restore(state);return value;
}
hal_result_t hal_uart_write(const void *data,size_t size,unsigned timeout_ms,size_t *written) {
    if(!data && size)return HAL_INVALID;
    const uint8_t *p=data;size_t n=0;uint32_t start=hal_time_ms();
    while(n<size) {
        if(!uart_txfull_read())uart_rxtx_write(p[n++]);
        else if((uint32_t)(hal_time_ms()-start)>=timeout_ms) {if(written)*written=n;return HAL_TIMEOUT;}
    }
    if(written) {*written=n;}
    return HAL_OK;
}
#if MINI_FEATURE_BOARD_IO
void hal_leds_set(unsigned mask) {board_io_leds_write(mask&63);}
#endif
#if MINI_FEATURE_BOARD_IO
unsigned hal_leds_get(void) {return board_io_leds_read();}
#endif
#if MINI_FEATURE_BOARD_IO
unsigned hal_buttons_read(void) {return board_io_buttons_read();}
#endif
#if MINI_FEATURE_BOARD_IO
unsigned hal_switches_read(void) {return board_io_switches_read();}
#endif
void hal_buttons_take(unsigned *pressed,unsigned *released) {
    unsigned state=hal_irq_save();if(pressed)*pressed=presses;if(released)*released=releases;presses=releases=0;hal_irq_restore(state);
}
#if MINI_FEATURE_WS2812
hal_result_t hal_ws2812_set(uint8_t red,uint8_t green,uint8_t blue) {
    if(ws2812_busy_read())return HAL_BUSY;
    ws2812_color_write(((unsigned)red<<16)|((unsigned)green<<8)|blue);ws2812_send_write(1);return HAL_OK;
}
unsigned hal_ws2812_busy(void) {return ws2812_busy_read();}
#else
hal_result_t hal_ws2812_set(uint8_t red,uint8_t green,uint8_t blue) {(void)red;(void)green;(void)blue;return HAL_UNSUPPORTED;}
unsigned hal_ws2812_busy(void) {return 0;}
#endif
#if !MINI_FEATURE_BOARD_IO
void hal_leds_set(unsigned mask) {(void)mask;}
unsigned hal_leds_get(void) {return 0;}
unsigned hal_buttons_read(void) {return 0;}
unsigned hal_switches_read(void) {return 0;}
#endif
hal_result_t hal_phys_reset(unsigned hold_ms) {
    if(!hold_ms || hold_ms>1000)return HAL_INVALID;
    hal_eth_stop();
    hal_usb_stop();
#if MINI_FEATURE_ETH || MINI_FEATURE_USB
    phy_reset_out_write(0);hal_delay_ms(hold_ms<10?10:hold_ms);phy_reset_out_write(1);
    hal_delay_ms(50);hal_eth_init();hal_usb_init();return HAL_OK;
#else
    return HAL_UNSUPPORTED;
#endif
}
void hal_reboot(void) {
    hal_eth_stop();
    hal_usb_stop();
    hal_audio_stop();
    hal_mic_stop();
    hal_video_stop();hal_irq_save();uart_ev_enable_write(0);timer1_ev_enable_write(0);
#if MINI_FEATURE_BOARD_IO
    board_io_ev_enable_write(0);
#endif
    __asm__ volatile("csrw 0xbc0,zero\ncsrw mie,zero":::"memory");ctrl_reset_write(1);for(;;) {}
}
