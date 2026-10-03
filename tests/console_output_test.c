/* Native regression for real driver fan-out and BIOS terminal buffer:
 * cc -DMINI_BOOTLOADER=0 -DMINI_FEATURE_USB=0 -ffunction-sections
 *    -fdata-sections -Wl,--gc-sections -I build/base/software/include
 *    -I firmware/hal/include tests/console_output_test.c -o console-test
 * CSR bodies are replaced below; no physical hardware is exercised. */
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
int main(void) {
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
    assert(!memcmp(screen[0],"BIOS",4));
    assert(!memcmp(screen[1],"driver 1234abcd",15));
    assert(!strcmp(uart,"BIOS\r\ndriver 1234abcd"));
    puts("PASS driver/BIOS output mirrored exactly once into actual TTY buffer; detach works");
}
