/* Board management smoke test. No Flash program/erase or EEPROM data writes. */
#include <stdint.h>
#include "ae350_address_map.h"
#include "board_management.h"
#define W(a) (*(volatile uint32_t *)(uintptr_t)(a))
static volatile uint32_t *const r=(volatile uint32_t *)AE350_RESULTS_BASE;
static struct board_flash_info flash;
static uint8_t buffer[544],eeprom[16];
static uint8_t single[512];
static void putc(char c) {while(!(W(0xf0300034u)&0x20u)) {} W(0xf0300020u)=(uint8_t)c;}
static void puts(const char *s) {while(*s)putc(*s++);}
static uint32_t crc(const uint8_t *p,unsigned bytes) {
    uint32_t c=~0u;while(bytes--) {c^=*p++;for(unsigned b=0;b<8;b++)c=(c>>1)^(0xedb88320u&-(c&1));}return ~c;
}
void main(void) {
    for(unsigned i=0;i<48;i++)r[i]=0;
    uint32_t cache;__asm__ volatile("csrr %0,0x7ca":"=r"(cache));r[30]=cache;
    r[0]=0x42550003;r[31]=0x42550003;
    r[1]=board_flash_init(&flash);
    r[2]=flash.controller;r[3]=flash.jedec;r[4]=flash.bytes;r[5]=flash.status;r[6]=flash.sfdp;r[27]=flash.hz;
    if(!r[1]) {
        buffer[0]=buffer[513]=0xa5;
        r[29]=board_flash_read(0x100,buffer+1,512);
        if(!r[29])r[7]=crc(buffer+1,512);
        int second=board_flash_read_single(0x100,buffer+1,512);
        if(second)r[29]=second;else r[8]=crc(buffer+1,512);
        r[9]=buffer[0]!=0xa5 || buffer[513]!=0xa5;
        r[25]=board_flash_read(flash.bytes,buffer,1);
        for(unsigned page=0;page<32;page++) {
            uint32_t offset=((flash.bytes-512u)*page)/31u;
            int rc=board_flash_read(offset,buffer+1,512);
            if(!rc)rc=board_flash_read_single(offset,single,512);
            if(rc) {r[46]=(uint32_t)rc;break;}
            unsigned nonempty=0;
            for(unsigned i=0;i<512;i++) {
                if(buffer[i+1]!=single[i])r[46]++;
                if(single[i]!=255)nonempty=1;
            }
            r[44]++;r[45]+=nonempty;
        }
    }
    r[10]=board_i2c_init();r[11]=W(AE350_I2C_BASE);r[12]=W(AE350_I2C_BASE+0x18);
    if(!r[10]) {
        r[13]=board_twi_mux_get();r[21]=W(AE350_GPIO_BASE+0x24)&AE350_TWI_MUX_GPIO_MASK;
        r[14]=board_i2c_probe(0x50);
        r[15]=board_i2c_read_register(0x50,0,eeprom,16);
        if(!r[15])r[16]=crc(eeprom,16);
        int rc=board_i2c_read_register(0x50,0,eeprom,16);
        if(rc)r[15]=rc;else r[17]=crc(eeprom,16);
        for(unsigned channel=0;channel<8;channel++) {
            int selected=board_twi_mux_select(channel);
            if(selected) {r[42]=channel+1;break;}
            if(board_twi_mux_get()==channel)r[41]|=1u<<channel;
            int probe=board_i2c_probe(0x60);r[32+channel]=probe;
            if(!probe)r[40]|=1u<<channel;
            else if(probe!=BUS_NACK) {r[42]=channel+1;break;}
            int storage=board_i2c_probe(0x50);
            if(!storage)r[43]|=1u<<channel;
            else if(storage!=BUS_NACK) {r[42]=channel+1;break;}
        }
        if(board_twi_mux_select(2))r[42]=9;
        r[18]=board_i2c_probe(0x55);r[19]=W(AE350_I2C_BASE+0x18);
        r[26]=board_twi_mux_select(8);
    }
    r[20]=W(AE350_I2C_BASE+0x10);r[28]=W(AE350_I2C_BASE+0x2c);
    r[24]=board_management_stop();r[22]=W(AE350_SPI_BASE+0x10);r[23]=W(AE350_I2C_BASE+0x18);
    __asm__ volatile("fence":::"memory");r[0]=0x4255ffff;
    puts("AE350 BUS DONE\r\n");
    for(;;)__asm__ volatile("wfi");
}
