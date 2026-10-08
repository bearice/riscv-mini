/* UART-loaded DDR utility. Addresses come from the selected build's csr.json.
 * Write support is compiled only for the explicit update operation.
 */
#include <stdint.h>
#include "probe_config.h"

static uint32_t read_reg(uint32_t address) { return *(volatile uint32_t *)address; }
static void write_reg(uint32_t address, uint32_t value) { *(volatile uint32_t *)address=value; }
static void put(char value) {
    while(read_reg(UART_TXFULL)) {}
    write_reg(UART_RXTX,(uint8_t)value);
}
static void text(const char *value) { while(*value)put(*value++); }
static unsigned get(void) {
    while(read_reg(UART_RXEMPTY)) {}
    unsigned value=read_reg(UART_RXTX)&255u;
    /* LiteX's event-driven RX FIFO advances on ev.rx.clear, not RXTX read. */
    write_reg(UART_EV_PENDING,2u);
    return value;
}
#if PROBE_XIP
static unsigned quiesced;
static void quiesce(void) {
    /* Code, data and stack are in DDR and interrupts were disabled at entry.
     * Disable new transactions, allow an in-flight transaction to finish,
     * then hand idle software SPI pads to the external programmer.
     */
    write_reg(XIP_ENABLE,0u);
    __asm__ volatile("fence rw,rw" ::: "memory");
    unsigned budget=10000000u;
    while(read_reg(XIP_BUSY) && --budget) {}
    if(!budget || read_reg(XIP_ENABLE)!=0u) {
        text("ERR XIP QUIESCE\r\n> ");return;
    }
    write_reg(FLASH_CS,0x10000u);
    __asm__ volatile("fence rw,rw" ::: "memory");
    if(read_reg(FLASH_CS)!=0x10000u || read_reg(XIP_BUSY)) {
        text("ERR SPI OWNERSHIP\r\n> ");return;
    }
    quiesced=1;
    text("XIP QUIESCED enable=0 busy=0 spi_cs=idle\r\n> ");
}
#endif
#if !PROBE_XIP || PROBE_WRITE
static unsigned transfer(unsigned value,unsigned bits) {
    write_reg(FLASH_MOSI,value);write_reg(FLASH_CONTROL,(bits<<8)|1u);
    unsigned budget=1000000u;
    while(!(read_reg(FLASH_STATUS)&1u) && --budget) {}
    if(!budget || (read_reg(FLASH_STATUS)&4u)) {
        write_reg(FLASH_CS,0x10000u);text("READBACK IO ERROR\r\n");for(;;){}
    }
    return read_reg(FLASH_MISO);
}
#endif
#if PROBE_WRITE
static unsigned status(void) {
    write_reg(FLASH_CS,0x10001u);transfer(5,8);
    unsigned value=transfer(0xff,8);write_reg(FLASH_CS,0x10000u);return value;
}
static void ready(void) {
    unsigned budget=2000000u;
    while((status()&1u) && --budget) {}
    if(!budget) {text("ERR FLASH TIMEOUT\r\n");for(;;){}}
}
static void enable_write(void) {
    ready();write_reg(FLASH_CS,0x10001u);transfer(6,8);write_reg(FLASH_CS,0x10000u);
    if(!(status()&2u)) {text("ERR FLASH WEL\r\n");for(;;){}}
}
static unsigned word(void) {
    unsigned value=0;for(unsigned i=0;i<4;i++)value|=get()<<(i*8);return value;
}
static unsigned crc_byte(unsigned crc,unsigned value) {
    crc^=value;for(unsigned i=0;i<8;i++)crc=(crc>>1)^((0u-(crc&1u))&0xedb88320u);return crc;
}
static void install(void) {
    unsigned offset=word(),length=word();
    if(!quiesced || (offset!=PROBE_OFFSET0 && offset!=PROBE_OFFSET1) ||
       length!=(offset==PROBE_OFFSET0?PROBE_LENGTH0:PROBE_LENGTH1)) {
        text("ERR WRITE RANGE\r\n> ");return;
    }
    write_reg(FLASH_DIVIDER,6u);
    /* Three independent ID transactions must agree before the first erase. */
    for(unsigned i=0;i<3;i++) {
        write_reg(FLASH_CS,0x10001u);transfer(0x9f,8);
        unsigned id=transfer(0xffffff,24);write_reg(FLASH_CS,0x10000u);
        if(id!=0x0b4017u) {text("ERR WRITE JEDEC\r\n> ");return;}
    }
    text("ERASING\r\n");
    for(unsigned address=offset;address<offset+length;address+=4096) {
        enable_write();write_reg(FLASH_CS,0x10001u);
        transfer((0x20u<<24)|address,32);write_reg(FLASH_CS,0x10000u);ready();
    }
    text("READY WRITE\r\n");
    unsigned sequence=0,done=0;
    while(done<length) {
        unsigned crc=0xffffffffu,seq=0,count=0;
        for(unsigned i=0;i<6;i++) {
            unsigned value=get();crc=crc_byte(crc,value);
            if(i<4)seq|=value<<(i*8);else count|=value<<((i-4)*8);
        }
        uint8_t data[128];
        unsigned wanted=length-done;if(wanted>128)wanted=128;
        if(seq!=sequence || count!=wanted) {
            text("ERR WRITE FRAME\r\n");for(;;){}
        }
        for(unsigned i=0;i<count;i++) {data[i]=get();crc=crc_byte(crc,data[i]);}
        if(word()!=(crc^0xffffffffu)) {text("ERR WRITE CRC\r\n");for(;;){}}
        enable_write();write_reg(FLASH_CS,0x10001u);
        transfer((2u<<24)|(offset+done),32);
        for(unsigned i=0;i<count;i++)transfer(data[i],8);
        write_reg(FLASH_CS,0x10000u);ready();
        /* Confirm each page fragment through software SPI before ACK. */
        write_reg(FLASH_CS,0x10001u);transfer((3u<<24)|(offset+done),32);
        unsigned equal=1;
        for(unsigned i=0;i<count;i++)if((transfer(0xff,8)&255u)!=data[i])equal=0;
        write_reg(FLASH_CS,0x10000u);
        if(!equal) {text("ERR WRITE VERIFY\r\n");for(;;){}}
        put('K');for(unsigned i=0;i<4;i++)put(seq>>(i*8));
        done+=count;sequence++;
    }
    text("WRITE VERIFIED\r\n> ");
}
#endif
static void dump(unsigned offset,unsigned count) {
#if PROBE_XIP
    const volatile uint8_t *source=(const volatile uint8_t *)(PROBE_BASE+offset);
    while(count--)put(*source++);
#else
    write_reg(FLASH_DIVIDER,6u);write_reg(FLASH_CS,0x10001u);
    transfer((3u<<24)|offset,32);
    while(count--)put(transfer(0xffu,8));
    write_reg(FLASH_CS,0x10000u);
#endif
}
int main(void) {
    text("SYSTEM READY - FLASH READBACK\r\n> ");
    for(;;) {
        unsigned command=get();
#if PROBE_WRITE
        if(command=='w') {install();continue;}
#endif
        if(command=='r') {
#if PROBE_XIP
            if(quiesced) {text("ERR XIP DISABLED\r\n> ");continue;}
#endif
            text("READBACK BEGIN\r\n");
            dump(PROBE_OFFSET0,PROBE_LENGTH0);
            dump(PROBE_OFFSET1,PROBE_LENGTH1);
            text("READBACK END\r\n> ");
#if PROBE_XIP
        } else if(command=='q') {
            quiesce();
#endif
        } else if(command=='!') {
#if PROBE_XIP
            write_reg(FLASH_CS,0x10000u);
            write_reg(XIP_ENABLE,1u);
            __asm__ volatile("fence rw,rw" ::: "memory");
#endif
            write_reg(CTRL_RESET,1u);for(;;){}
        }
    }
}
