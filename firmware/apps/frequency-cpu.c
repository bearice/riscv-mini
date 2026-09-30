/* Isolated CPU/Wishbone/ROM/SRAM/timer/UART qualification, no DDR. */
#include <stdint.h>
#include "mini_csr.h"
#include <generated/soc.h>

static volatile uint32_t scratch[2048];
static void putc_uart(char c) {
    while (mini_uart_txfull_read()) {}
    mini_uart_rxtx_write((unsigned char)c);
}
static void puts_uart(const char *s) { while (*s) putc_uart(*s++); }
static void hex(uint32_t n) {
    const char *digits="0123456789abcdef";
    for (int bit=28;bit>=0;bit-=4) putc_uart(digits[(n>>bit)&15]);
}
static uint32_t ticks(void) { mini_timer0_update_value_write(1); return ~mini_timer0_value_read(); }
static void check(uint32_t seed) {
    uint32_t start=ticks(),state=seed+0x12345678u,hash=2166136261u;
    for (uint32_t i=0;i<2048;++i) scratch[i]=seed^(i*0x9e3779b9u);
    for (uint32_t i=0;i<8192;++i) {
        state=state*1664525u+1013904223u;
        uint32_t index=(state>>16)&2047u,value=scratch[index],shift=i%31u+1u;
        uint32_t rotated=(value<<shift)|(value>>(32u-shift));
        scratch[index]=(rotated^((value^state)/shift))+i;
    }
    for (uint32_t i=0;i<2048;++i) hash=(hash^scratch[i])*16777619u;
    uint32_t elapsed=ticks()-start;
    puts_uart("CPU CHECK seed=");hex(seed);puts_uart(" result=");hex(hash);
    puts_uart(" ticks=");hex(elapsed);puts_uart("\r\n");
}
int main(void) {
    mini_timer0_en_write(0);mini_timer0_load_write(~0u);mini_timer0_reload_write(~0u);mini_timer0_en_write(1);
    puts_uart("\r\nriscv-mini M0 | RV32IM | " MINI_SYS_MHZ " MHz | frequency experiment\r\n");
    check(42);puts_uart("> ");
    uint32_t seed=0;unsigned command=0;
    for (;;) {
        if (!mini_uart_rxempty_read()) {
            unsigned c=mini_uart_rxtx_read();mini_uart_ev_pending_write(2);
            if (c=='\r') {
                puts_uart("\r\n");if(command)check(seed);command=0;seed=0;puts_uart("> ");
            } else {
                putc_uart((char)c);
                if (c=='f'&&!command) {command=1;seed=0;}
                else if(command) {
                    unsigned digit=c>='0'&&c<='9'?c-'0':c>='a'&&c<='f'?c-'a'+10:16;
                    if(digit<16)seed=(seed<<4)|digit;
                }
            }
        }
    }
}
