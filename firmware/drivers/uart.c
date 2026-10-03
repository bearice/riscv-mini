#include "io.h"
#include <generated/csr.h>
#if !MINI_BOOTLOADER
static void (*console_mirror)(char);
void io_console_mirror(void (*callback)(char)) {console_mirror=callback;}
#endif
void putchar_uart(char value) {
    while(uart_txfull_read()) {} uart_rxtx_write((uint8_t)value);
#if !MINI_BOOTLOADER
    if(console_mirror)console_mirror(value);
#endif
}
void puts_uart(const char *text) {while(*text) putchar_uart(*text++);}
void io_hex(uint32_t value) {const char *d="0123456789abcdef";for(int i=28;i>=0;i-=4) putchar_uart(d[(value>>i)&15]);}
