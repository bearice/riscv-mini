#include "io.h"
#include <generated/csr.h>

void spi_select(unsigned lcd, unsigned selected) {
#if MINI_FEATURE_SPI_LCD
    if (lcd) lcd_spi_cs_write(0x10000u | !!selected);
#endif

#if MINI_FEATURE_SD && !CONFIG_SD_NATIVE
    if(!lcd) spisdcard_cs_write(0x10000u | !!selected);
#endif
}
int spi_transfer(unsigned lcd, unsigned value, unsigned bits, unsigned *received) {
    if (lcd) {
#if MINI_FEATURE_SPI_LCD
        lcd_spi_mosi_write(value); lcd_spi_control_write((bits<<8)|1);
#else
        return 0;
#endif
    } else {

#if MINI_FEATURE_SD && !CONFIG_SD_NATIVE
        spisdcard_mosi_write(value); spisdcard_control_write((bits<<8)|1);
#else
        return 0;
#endif
    }
    uint32_t start=io_ticks();
    for (;;) {
        unsigned status=0;
#if MINI_FEATURE_SPI_LCD
        if(lcd)status=lcd_spi_status_read();
#endif
#if MINI_FEATURE_SD && !CONFIG_SD_NATIVE
        if(!lcd)status=spisdcard_status_read();
#endif
        if (status&4) { spi_select(lcd,0); return 0; }
        if (status&1) break;
        if ((uint32_t)(io_ticks()-start)>(CONFIG_CLOCK_FREQUENCY/100u)) { spi_select(lcd,0); return 0; }
    }
    if (received) {
#if MINI_FEATURE_SPI_LCD
        if(lcd)*received=lcd_spi_miso_read();
#endif
#if MINI_FEATURE_SD && !CONFIG_SD_NATIVE
        if(!lcd)*received=spisdcard_miso_read();
#endif
    }
    return 1;
}
