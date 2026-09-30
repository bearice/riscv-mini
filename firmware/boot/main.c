#include "mini_csr.h"
#ifdef MINI_DDR
#include <generated/csr.h>
#endif
#ifdef MINI_IO
#include "io.h"
#endif

void putchar_uart(char value) {
    while (mini_uart_txfull_read()) {}
    mini_uart_rxtx_write((unsigned char)value);
}

void puts_uart(const char *text) {
    while (*text) putchar_uart(*text++);
}

int main(void) {
#ifdef MINI_IO
    unsigned io_available=0, line_len=0, line_overflow=0;
    char line[64];
#endif
#ifdef MINI_DDR
    extern int ddr_bringup(void);
    /* Allow the programmer/UART bridge to settle after configuration. */
    for (volatile unsigned i=0; i<480000; ++i) {}
#ifdef MINI_STRESS
    puts_uart("\r\nriscv-mini M4 | RV32IM | 60 MHz | DDR 128 MiB\r\n");
#elif defined(MINI_VIDEO)
    puts_uart("\r\nriscv-mini M3 | RV32IM | 48 MHz | DDR 128 MiB\r\n");
#elif defined(MINI_IO)
    puts_uart("\r\nriscv-mini M2 | RV32IM | 48 MHz | DDR 128 MiB\r\n");
#else
    puts_uart("\r\nriscv-mini M1 | RV32IM | 48 MHz | DDR 128 MiB\r\n");
#endif
#ifdef MINI_VIDEO
    io_timer_init();
    int ddr_ok=video_stop() && ddr_bringup();
#else
    int ddr_ok=ddr_bringup();
#endif
    if (!ddr_ok) puts_uart("DDR FAILED: ROM UART recovery remains available.\r\n");
#ifdef MINI_IO
    io_timer_init();
    if (ddr_ok && spi_selftest()) {
        unsigned sd_ready=sd_mount_info();
        if (lcd_show(sd_ready,0)) {
            io_available=1;
            puts_uart("M2 READY: LCD transfer complete; SD ");
            puts_uart(sd_ready ? "mounted\r\n" : "unavailable\r\n");
        }
    }
#ifdef MINI_VIDEO
#ifdef MINI_STRESS
    if (ddr_ok) video_command("memcopy");
#endif
    if (ddr_ok && video_init()) {
#ifdef MINI_STRESS
        puts_uart("M4 READY: stress monitor\r\n");
#endif
    }
    puts_uart("Commands: fbinfo, fbflip, ");
#endif
    puts_uart("Commands: lcd, sdinfo, sdtest (create new), sdcheck FILE (read only), ! (reset)\r\n");
#endif
#else
    puts_uart("\r\nriscv-mini M0 | RV32IM | 48 MHz | UART 115200 8N1\r\n");
    puts_uart("ROM/SRAM bootstrap. DDR, SD and video are not enabled yet.\r\n");
#endif
    puts_uart("Type a character to echo; press reset to restart.\r\n> ");
    for (;;) {
        if (!mini_uart_rxempty_read()) {
            unsigned value = mini_uart_rxtx_read();
            mini_uart_ev_pending_write(2);
#ifdef MINI_DDR
            if (value == '!') {
#ifdef MINI_VIDEO
                if (!video_stop()) continue;
#endif
                puts_uart("\r\nCPU soft reset...\r\n");
                ctrl_reset_write(1);
                for (;;) {}
            }
#endif
            if (value == '\r') {
#ifdef MINI_IO
                line[line_len]=0;
                puts_uart("\r\n");
                if (io_available && !line_overflow) {
#ifdef MINI_VIDEO
                    if (!video_command(line))
#endif
                    sd_command(line);
                }
                line_len=line_overflow=0;
                puts_uart("> ");
#else
                puts_uart("\r\n> ");
#endif
            }
#ifdef MINI_IO
            else if (value == '\n') {}
#endif
            else putchar_uart((char)value);
#ifdef MINI_IO
            if (value!='\r' && value!='\n') {
                if (line_len<sizeof(line)-1) line[line_len++]=(char)value;
                else line_overflow=1;
            }
#endif
        }
    }
}
