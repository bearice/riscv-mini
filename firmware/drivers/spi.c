#include "io.h"
#include <generated/csr.h>

void io_timer_init(void) {
    timer0_en_write(0); timer0_load_write(0xffffffffu);
    timer0_reload_write(0xffffffffu); timer0_en_write(1);
}
uint32_t io_ticks(void) {
    timer0_update_value_write(1);
    return 0xffffffffu-timer0_value_read();
}
void io_delay_ms(unsigned ms) {
    uint32_t start=io_ticks();
    while ((uint32_t)(io_ticks()-start)<ms*(CONFIG_CLOCK_FREQUENCY/1000u)) {}
}
void io_hex(uint32_t x) {
    const char *digits="0123456789abcdef";
    for (int i=28; i>=0; i-=4) putchar_uart(digits[(x>>i)&15]);
}
void spi_select(unsigned lcd, unsigned selected) {
    if (lcd) lcd_spi_cs_write(0x10000u | !!selected);
    else spisdcard_cs_write(0x10000u | !!selected);
}
int spi_transfer(unsigned lcd, unsigned value, unsigned bits, unsigned *received) {
    if (lcd) { lcd_spi_mosi_write(value); lcd_spi_control_write((bits<<8)|1); }
    else { spisdcard_mosi_write(value); spisdcard_control_write((bits<<8)|1); }
    uint32_t start=io_ticks();
    for (;;) {
        unsigned status=lcd ? lcd_spi_status_read() : spisdcard_status_read();
        if (status&4) { spi_select(lcd,0); return 0; }
        if (status&1) break;
        if ((uint32_t)(io_ticks()-start)>(CONFIG_CLOCK_FREQUENCY/100u)) { spi_select(lcd,0); return 0; }
    }
    if (received) *received=lcd ? lcd_spi_miso_read() : spisdcard_miso_read();
    return 1;
}
int spi_selftest(void) {
    unsigned rx=0;
    spi_select(1,0); spi_select(0,0);
    lcd_spi_loopback_write(1); spisdcard_loopback_write(1);
    int ok=spi_transfer(1,0xa53c,16,&rx) && rx==0xa53c;
    ok=ok && spi_transfer(1,0x69,8,&rx) && (rx&255)==0x69;
    ok=ok && spi_transfer(0,0x96,8,&rx) && (rx&255)==0x96;
    lcd_spi_loopback_write(0); spisdcard_loopback_write(0);
    puts_uart(ok ? "SPI loopback PASS\r\n" : "SPI loopback FAIL\r\n");
    return ok;
}
