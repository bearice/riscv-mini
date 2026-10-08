/* Native regression for real driver fan-out and the BIOS TTY mirror:
 *
 *   cc -DMINI_BOOTLOADER=0 -DMINI_FEATURE_USB=0 -ffunction-sections
 *      -fdata-sections -Wl,--gc-sections -I build/base/software/include
 *      -I firmware/hal/include -I firmware/bios
 *      tests/console_output_test.c firmware/bios/vt.c -o console-test
 *
 * CSR bodies are replaced below and the few HAL entry points the TTY calls are
 * stubbed; no physical hardware is exercised. The escape-sequence coverage
 * lives in tests/vt_console_test.c. */
#include <stdint.h>
#include <assert.h>
#include <string.h>
#include <stdio.h>
#define __GENERATED_CSR_H
#define __GENERATED_SOC_H
static char uart[128],terminal[128];
static unsigned nuart,nterminal;
static unsigned uart_txfull_read(void) {return 0;}
static void uart_rxtx_write(uint8_t c) {uart[nuart++]=c;}
#include "../firmware/drivers/uart.c"
#include "../firmware/bios/console.c"
void hal_console_mirror(void (*callback)(char)) {io_console_mirror(callback);}
void hal_uart_putc(char c) {putchar_uart(c);}
static void mirror(char c) {terminal[nterminal++]=c;}
/* HAL stubs: the TTY only reaches these when a video device exists, which this
 * test never sets up (bios_console_init(0)). */
void hal_poll(void) {}
uint32_t hal_time_ms(void) {return 0;}
int hal_uart_getc(void) {return -1;}
volatile uint16_t *hal_video_frame(unsigned slot) {(void)slot;return 0;}
hal_result_t hal_video_present(unsigned slot) {(void)slot;return HAL_UNSUPPORTED;}
void tests_video_stop(void) {}
/* Read one TTY row as a NUL-terminated, right-trimmed string. */
static void term_row(unsigned r,char *out) {
    int c;
    for(c=0;c<VT_COLS;++c)out[c]=term.grid[r][c].ch;
    out[VT_COLS]=0;
    while(c>0 && out[c-1]==' ')out[--c]=0;
}
int main(void) {
    char line0[VT_COLS+1],line1[VT_COLS+1];
    io_console_mirror(mirror);
    puts_uart("status ");io_hex(0x1234abcd);putchar_uart('\n');
    assert(nuart==nterminal);
    assert(!memcmp(uart,terminal,nuart));
    assert(!strcmp(terminal,"status 1234abcd\n"));
    io_console_mirror(0);puts_uart("UART only");
    assert(nuart==nterminal+9);
    nuart=0;memset(uart,0,sizeof(uart));
    bios_console_init(0);
    bios_puts("BIOS\r\n");puts_uart("driver ");io_hex(0x1234abcd);
    term_row(0,line0);term_row(1,line1);
    assert(!strcmp(line0,"BIOS"));
    assert(!strcmp(line1,"driver 1234abcd"));
    assert(!strcmp(uart,"BIOS\r\ndriver 1234abcd"));
    /* The mirror is now the VT parser: escapes must reach the grid, not the row. */
    bios_puts("\033[2J\033[H");
    term_row(0,line0);
    assert(!strcmp(line0,""));
    puts("PASS driver/TTY fan-out mirrored exactly once into the VT grid; detach works");
    return 0;
}
