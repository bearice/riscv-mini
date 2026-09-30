/* Independent UART-loaded acceptance example, not part of the product monitor. */
#include <hal/hal.h>
static volatile unsigned exceptions;
void hal_exception_handler(hal_trap_frame_t *frame) {
    if(frame->cause==11) {++exceptions;frame->pc+=4;return;}
    hal_uart_puts("HAL DEMO FAULT\r\n");for(;;) {}
}
int main(void) {
    hal_init();
    __asm__ volatile("ecall":::"memory");
    uint32_t started=hal_time_ms();hal_stats_t before,after;hal_get_stats(&before);
    volatile unsigned calculation=0;
    for(unsigned i=0;i<100000;++i)calculation=calculation*33u+i;
    hal_delay_ms(30);hal_get_stats(&after);
    if(exceptions!=1 || after.timer_irqs-before.timer_irqs<20 || after.unhandled_irqs || calculation!=0x73cfeab0u || hal_time_ms()-started<30
       || !hal_deadline_reached(5,0xfffffff0u) || hal_deadline_reached(0xfffffff0u,5)) {
        hal_uart_puts("HAL DEMO FAIL\r\n");for(;;) {}
    }
    hal_uart_puts("HAL DEMO PASS: ECALL resume / timer IRQ / live DDR calculation\r\n");
    hal_uart_puts("SYSTEM READY\r\n> ");
    for(;;) {
        int ch=hal_uart_getc();if(ch=='!')hal_reboot();
        if(ch>=0) {hal_uart_puts("UART IRQ ECHO=");hal_uart_hex(ch);hal_uart_puts("\r\n> ");}
    }
}
