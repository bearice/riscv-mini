/* ROM-only SPI NOR driver: byte transfers share one compact command path.
 * App keeps the faster word-transfer driver and factory-UID support. */
#include "io.h"
#include "flash.h"
#include <generated/csr.h>

static unsigned capacity;
unsigned flash_size(void) { return capacity; }
static void select_flash(unsigned active) { flash_spi_cs_write(0x10000u|active); }
static int byte(unsigned value) {
    flash_spi_mosi_write(value);
    flash_spi_control_write((8u<<8)|1u);
    uint32_t start=io_ticks();
    while(!(flash_spi_status_read()&1u))
        if((uint32_t)(io_ticks()-start)>CONFIG_CLOCK_FREQUENCY/100u) return -1;
    if(flash_spi_status_read()&4u) return -1;
    return flash_spi_miso_read()&255u;
}
static int command(unsigned value) {
    select_flash(1);int ok=byte(value)>=0;select_flash(0);return ok;
}
static int status(void) {
    select_flash(1);int value=byte(5)>=0?byte(0xff):-1;select_flash(0);return value;
}
static int ready(unsigned ms) {
    uint32_t start=io_ticks();
    do {
        int value=status();
        if(value<0) return 0;
        if(!(value&1)) return 1;
    } while((uint32_t)(io_ticks()-start)<ms*(CONFIG_CLOCK_FREQUENCY/1000u));
    return 0;
}
static int write_enable(void) {
    if(!ready(3000) || !command(6)) return 0;
    int value=status();return value>=0 && (value&2);
}
static int address_command(unsigned command, unsigned address) {
    select_flash(1);
    if(byte(command)<0) return 0;
    for(int shift=16;shift>=0;shift-=8) if(byte(address>>shift)<0) return 0;
    return 1;
}
static int writable(unsigned address,unsigned length) {
    return capacity>=MINI_FLASH_SIZE && address>=MINI_FLASH_WRITABLE
        && address<MINI_FLASH_SIZE && length<=MINI_FLASH_SIZE-address;
}
int flash_init(uint32_t *id) {
    capacity=0;*id=0;select_flash(0);flash_spi_clk_divider_write(6);
    io_delay_ms(1);
    if(!command(0xab)) return 0;
    io_delay_ms(1);select_flash(1);
    int ok=byte(0x9f)>=0;
    for(unsigned i=0;ok && i<3;++i) {
        int value=byte(0xff);if(value<0) ok=0;else *id=(*id<<8)|value;
    }
    select_flash(0);
    if(ok && (*id==0xef4016u || *id==0xef7016u || *id==0x0b4017u) && ready(3000)) {
        capacity=1u<<(*id&255u);return 1;
    }
    return 0;
}
int flash_read(unsigned address,void *data,unsigned length) {
    if(address>=capacity || length>capacity-address) return 0;
    uint8_t *p=data;int ok=address_command(3,address);
    while(ok && length--) {int value=byte(0xff);if(value<0) ok=0;else *p++=value;}
    select_flash(0);return ok;
}
int flash_erase_sector(unsigned address) {
    if((address&4095u) || !writable(address,4096) || !write_enable()) return 0;
    int ok=address_command(0x20,address);select_flash(0);return ok && ready(3000);
}
int flash_program(unsigned address,const void *data,unsigned length) {
    if(!writable(address,length)) return 0;
    const uint8_t *p=data;
    while(length) {
        unsigned count=256-(address&255u);if(count>length) count=length;
        if(!write_enable()) return 0;
        int ok=address_command(2,address);
        for(unsigned i=0;ok && i<count;++i) ok=byte(p[i])>=0;
        select_flash(0);if(!ok || !ready(100)) return 0;
        address+=count;p+=count;length-=count;
    }
    return 1;
}
