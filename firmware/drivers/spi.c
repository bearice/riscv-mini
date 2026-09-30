#include "io.h"
#include <generated/csr.h>

void spi_select(unsigned lcd, unsigned selected) {
    if (lcd) lcd_spi_cs_write(0x10000u | !!selected);

#if !CONFIG_SD_NATIVE
    else spisdcard_cs_write(0x10000u | !!selected);
#endif
}
int spi_transfer(unsigned lcd, unsigned value, unsigned bits, unsigned *received) {
    if (lcd) { lcd_spi_mosi_write(value); lcd_spi_control_write((bits<<8)|1); }

#if !CONFIG_SD_NATIVE
    else { spisdcard_mosi_write(value); spisdcard_control_write((bits<<8)|1); }
#else
    else return 0;
#endif
    uint32_t start=io_ticks();
    for (;;) {
        unsigned status=lcd_spi_status_read();
#if !CONFIG_SD_NATIVE
        if(!lcd)status=spisdcard_status_read();
#endif
        if (status&4) { spi_select(lcd,0); return 0; }
        if (status&1) break;
        if ((uint32_t)(io_ticks()-start)>(CONFIG_CLOCK_FREQUENCY/100u)) { spi_select(lcd,0); return 0; }
    }
    if (received) {
        *received=lcd_spi_miso_read();
#if !CONFIG_SD_NATIVE
        if(!lcd)*received=spisdcard_miso_read();
#endif
    }
    return 1;
}
