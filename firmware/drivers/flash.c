/* Compatible SPI NOR. Physical size is detected; writes stay in [2,4) MiB. */
#include "io.h"
#include "flash.h"
#include <generated/csr.h>
static unsigned capacity;
static uint32_t jedec;
unsigned flash_size(void) { return capacity; }
static void select_flash(unsigned active) { flash_spi_cs_write(0x10000u|!!active); }
static int transfer(unsigned value, unsigned bits, unsigned *received) {
    flash_spi_mosi_write(value); flash_spi_control_write((bits<<8)|1);
    uint32_t start=io_ticks();
    while (!(flash_spi_status_read()&1u)) {
        if ((uint32_t)(io_ticks()-start)>CONFIG_CLOCK_FREQUENCY/100u) { select_flash(0);return 0; }
    }
    if (flash_spi_status_read()&4u) { select_flash(0);return 0; }
    if(received) *received=flash_spi_miso_read();
    return 1;
}
static int instruction(unsigned command) {
    select_flash(1);int ok=transfer(command,8,0);select_flash(0);return ok;
}
static int status(unsigned *value) {
    select_flash(1);int ok=transfer(5,8,0)&&transfer(0xff,8,value);select_flash(0);return ok;
}
static int ready(unsigned milliseconds) {
    uint32_t start=io_ticks();unsigned value;
    do {
        if(!status(&value)) return 0;
        if(!(value&1u)) return 1;
    } while((uint32_t)(io_ticks()-start)<milliseconds*(CONFIG_CLOCK_FREQUENCY/1000u));
    return 0;
}
static int writable(unsigned address, unsigned length) {
    return capacity>=MINI_FLASH_SIZE && address>=MINI_FLASH_WRITABLE && address<MINI_FLASH_SIZE && length<=MINI_FLASH_SIZE-address;
}
static int write_enable(void) {
    unsigned value;
    return ready(3000) && instruction(6) && status(&value) && (value&2u);
}
int flash_init(uint32_t *id) {
    capacity=0;
    select_flash(0);flash_spi_clk_divider_write(6);
    io_delay_ms(1);
    if(!instruction(0xab)) return 0;
    io_delay_ms(1);
    unsigned value=0;
    select_flash(1);int ok=transfer(0x9f,8,0)&&transfer(0xffffff,24,&value);select_flash(0);
    if(id) *id=value;
    jedec=ok?value:0;
    /* Restrict IDs to supported 3.3 V parts; don't guess erase geometry for unknown devices. */
    int supported=value==0xef4016u || value==0xef7016u || value==0x0b4017u;
    if(ok && supported && ready(3000)) {capacity=1u<<(value&255u);return 1;}
    return 0;
}
int flash_read(unsigned address, void *data, unsigned length) {
    if(address>=capacity || length>capacity-address) return 0;
    uint8_t *p=data;unsigned value;
    select_flash(1);int ok=transfer((3u<<24)|address,32,0);
    while(ok && length>=4) {
        ok=transfer(0xffffffffu,32,&value);
        if(ok) { *p++=value>>24;*p++=value>>16;*p++=value>>8;*p++=value;length-=4; }
    }
    while(ok && length) { ok=transfer(0xff,8,&value);if(ok) {*p++=value;--length;} }
    select_flash(0);return ok;
}
int flash_erase_sector(unsigned address) {
    if((address&4095u) || !writable(address,4096) || !write_enable()) return 0;
    select_flash(1);int ok=transfer((0x20u<<24)|address,32,0);select_flash(0);
    return ok && ready(3000);
}
int flash_program(unsigned address, const void *data, unsigned length) {
    if(!writable(address,length)) return 0;
    const uint8_t *p=data;
    while(length) {
        unsigned count=256-(address&255u);if(count>length) count=length;
        if(!write_enable()) return 0;
        select_flash(1);int ok=transfer((2u<<24)|address,32,0);
        for(unsigned i=0;ok && i<count;++i) ok=transfer(p[i],8,0);
        select_flash(0);if(!ok || !ready(100)) return 0;
        address+=count;p+=count;length-=count;
    }
    return 1;
}

/* Factory UID, not a user-programmed data sector. XTX B uses SFDP 0x194;
   supported Winbond parts use 4B + four dummy bytes and an 8-byte UID. */
int flash_uid(uint8_t uid[16],unsigned *length) {
    if(!uid || !length || !capacity)return 0;
    *length=0;unsigned count=jedec==0x0b4017u?16u:8u,value;
    select_flash(1);
    int ok=jedec==0x0b4017u ? transfer(0x5a000194u,32,0)&&transfer(0xff,8,0) :
        transfer(0x4b,8,0)&&transfer(0xffffffffu,32,0);
    unsigned any=0,not_ff=0;
    for(unsigned i=0;ok && i<count;++i) {
        ok=transfer(0xff,8,&value);
        if(ok) {uid[i]=value;any|=uid[i];not_ff|=uid[i]^255u;}
    }
    select_flash(0);if(!ok || !any || !not_ff)return 0;
    for(unsigned i=count;i<16;++i)uid[i]=0;
    *length=count;return 1;
}
