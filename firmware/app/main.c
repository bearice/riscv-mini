/* Minimal DDR-resident base application; device APIs live in drivers/. */
#include <generated/csr.h>
#include <string.h>
#include "io.h"
#include "flash.h"
static void status(void) {
    puts_uart("CPU/sys=60 MHz DDR=120 MHz LCD=9 MHz UART=115200\r\n");
    uint32_t id=0;unsigned ok=flash_init(&id);
    puts_uart("FLASH JEDEC=");io_hex(id);puts_uart(" bytes=");io_hex(flash_size());puts_uart(ok?" READY\r\n":" UNAVAILABLE\r\n");
    video_status();
}
int main(void) {
    io_timer_init();unsigned sd_ready=sd_mount();unsigned spi_lcd_ready=lcd_show(sd_ready);unsigned rgb_ready=video_init();
    puts_uart("\r\nriscv-mini | RV32IM | DDR application at 40800000\r\n");
    puts_uart("SYSTEM READY sd=");io_hex(sd_ready);puts_uart(" spi_lcd=");io_hex(spi_lcd_ready);
    puts_uart(" rgb_lcd=");io_hex(rgb_ready);puts_uart("\r\n");
    status();puts_uart("Commands: help, status, ls, reboot (! also resets)\r\n> ");
    char line[32];unsigned used=0,overflow=0;
    for(;;) {
        if(uart_rxempty_read()) continue;
        unsigned ch=uart_rxtx_read();uart_ev_pending_write(2);
        if(ch=='!') {video_stop();ctrl_reset_write(1);for(;;) {}}
        if(ch=='\n') continue;
        if(ch=='\r') {
            line[used]=0;puts_uart("\r\n");
            if(overflow) puts_uart("ERR command too long\r\n");
            else if(!strcmp(line,"reboot")) {video_stop();ctrl_reset_write(1);for(;;) {}}
            else if(!strcmp(line,"status")) status();
            else if(!strcmp(line,"ls")) sd_list();
            else if(!strcmp(line,"help")) puts_uart("help, status, ls, reboot\r\n");
            else if(used) puts_uart("ERR unknown command\r\n");
            used=overflow=0;puts_uart("> ");
        } else if(ch==8 || ch==127) {if(used) {--used;puts_uart("\b \b");}}
        else if(ch>=32 && ch<127) {putchar_uart(ch);if(used<sizeof(line)-1) line[used++]=ch;else overflow=1;}
    }
}
