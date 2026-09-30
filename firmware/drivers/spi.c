#include "io.h"
#include <generated/csr.h>

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
