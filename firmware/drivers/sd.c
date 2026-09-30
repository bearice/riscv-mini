/* SPI SD block transport. CRC7 commands, CRC16 data, bounded waits.
 * Writes are exposed only to FatFs; the monitor never writes arbitrary LBAs.
 */
#include "io.h"
#include "ff.h"
#include "diskio.h"
#include <hal/hal.h>
#include <generated/csr.h>

static unsigned initialized, high_capacity, io_failed, detect_present;
static uint32_t sectors;
static unsigned exchange(unsigned value) {
    unsigned rx=255;
    if (!spi_transfer(0,value,8,&rx)) io_failed=1;
    return rx&255;
}
static void release(void) { spi_select(0,0); exchange(255); }
static int ready(unsigned ms) {
    uint32_t start=io_ticks();
    do {
        if (exchange(255)==255 && !io_failed) return 1;
    } while (!io_failed && (uint32_t)(io_ticks()-start)<ms*(CONFIG_CLOCK_FREQUENCY/1000u));
    return 0;
}
static unsigned crc7(const unsigned char *p, unsigned len) {
    unsigned crc=0;
    for (unsigned i=0; i<len; ++i) {
        crc^=p[i];
        for (unsigned bit=0; bit<8; ++bit) crc=(crc<<1)^((crc&128)?0x12:0);
        crc&=255;
    }
    return crc|1;
}
static uint16_t crc16(const unsigned char *p, unsigned len) {
    uint16_t crc=0;
    for (unsigned i=0; i<len; ++i) {
        crc^=(uint16_t)p[i]<<8;
        for (unsigned bit=0; bit<8; ++bit) crc=(crc<<1)^((crc&0x8000)?0x1021:0);
    }
    return crc;
}
static unsigned command(unsigned cmd, uint32_t arg) {
    release(); spi_select(0,1);
    if (!ready(500)) return 255;
    unsigned char frame[5]={0x40|cmd,arg>>24,arg>>16,arg>>8,arg};
    for (unsigned i=0; i<5; ++i) exchange(frame[i]);
    exchange(crc7(frame,5));
    for (unsigned i=0; i<16 && !io_failed; ++i) {
        unsigned value=exchange(255);
        if (!(value&128)) return value;
    }
    return 255;
}
static int read_data(unsigned char *p, unsigned len) {
    uint32_t start=io_ticks();
    unsigned token;
    do {
        token=exchange(255);
        if (io_failed) return 0;
        if (token!=255) break;
    } while ((uint32_t)(io_ticks()-start)<CONFIG_CLOCK_FREQUENCY/2u);
    if (token!=0xfe) return 0;
    for (unsigned i=0; i<len; ++i) p[i]=exchange(255);
    unsigned received=exchange(255)<<8; received|=exchange(255);
    if (received!=crc16(p,len)) { puts_uart("SD data CRC16 error\r\n"); return 0; }
    return !io_failed;
}
DSTATUS disk_initialize(BYTE drive) {
    if (drive) return STA_NOINIT;
    initialized=high_capacity=io_failed=sectors=0;
    spisdcard_clk_divider_write((CONFIG_CLOCK_FREQUENCY+399999u)/400000u); /* 400 kHz during initialization. */
    spi_select(0,0);
    io_delay_ms(10);
    for (unsigned i=0; i<10; ++i) exchange(255);
    unsigned r=command(0,0);
    if (r!=1) { puts_uart("SD CMD0 failed R1="); io_hex(r); puts_uart("\r\n"); goto fail; }
    r=command(8,0x1aa);
    unsigned v2=r==1;
    if (v2) {
        unsigned echo=0;
        for (unsigned i=0; i<4; ++i) echo=(echo<<8)|exchange(255);
        if (echo!=0x1aa) goto fail;
    } else if (r!=5) goto fail;
    uint32_t start=io_ticks();
    do {
        r=command(55,0);
        if (r>1) goto fail;
        r=command(41,v2 ? 0x40000000u : 0);
        if (!r) break;
        if (r!=1) goto fail;
        release(); io_delay_ms(10);
    } while ((uint32_t)(io_ticks()-start)<2u*CONFIG_CLOCK_FREQUENCY);
    if (r) goto fail;
    if (command(58,0)) goto fail;
    uint32_t ocr=0;
    for (unsigned i=0; i<4; ++i) ocr=(ocr<<8)|exchange(255);
    if (!(ocr&0x80000000u) || !(ocr&0x00ff8000u)) goto fail;
    high_capacity=v2 && (ocr&0x40000000u);
    if (!high_capacity && command(16,512)) goto fail;
    if (command(59,1)) goto fail;
    unsigned char csd[16];
    if (command(9,0) || !read_data(csd,16)) goto fail;
    if ((csd[0]>>6)==1) {
        uint32_t size=((uint32_t)(csd[7]&63)<<16)|((uint32_t)csd[8]<<8)|csd[9];
        if (size==0x3fffffu) goto fail; /* 2 TiB needs 64-bit LBA support. */
        sectors=(size+1)*1024u;
    } else if ((csd[0]>>6)==0) {
        unsigned shift=(csd[5]&15)+((csd[9]&3)<<1)+(csd[10]>>7)+2;
        uint32_t size=((csd[6]&3)<<10)|(csd[7]<<2)|(csd[8]>>6);
        if (shift<9 || shift>31) goto fail;
        sectors=(size+1)<<(shift-9);
    } else goto fail;
    release();
    detect_present=sd_detect_in_read()&1;
    spisdcard_clk_divider_write((CONFIG_CLOCK_FREQUENCY+5999999u)/6000000u); /* 6 MHz first functional target. */
    initialized=1;
    puts_uart("SD init PASS: sectors="); io_hex(sectors);
    puts_uart(" SDHC="); io_hex(!!high_capacity);
    puts_uart(" detect="); io_hex(detect_present); puts_uart("\r\n");
    return 0;
fail:
    release(); puts_uart("SD unavailable: timeout/protocol error; UART remains available\r\n");
    return STA_NOINIT;
}
DSTATUS disk_status(BYTE drive) {
    if (drive || !initialized) return STA_NOINIT;
    if ((sd_detect_in_read()&1)!=detect_present) {
        initialized=0; return STA_NOINIT|STA_NODISK;
    }
    return 0;
}
DRESULT disk_read(BYTE drive, BYTE *buff, LBA_t sector, UINT count) {
    if (!count || sector>=sectors || count>sectors-sector) return RES_PARERR;
    if (disk_status(drive)) return RES_NOTRDY;
    io_failed=0;
    for (unsigned i=0; i<count; ++i) {
        uint32_t arg=high_capacity ? sector+i : (sector+i)*512;
        if (command(17,arg) || !read_data(buff+i*512,512)) {
            release(); initialized=0; return RES_ERROR;
        }
        release();
    }
    return RES_OK;
}
DRESULT disk_write(BYTE drive, const BYTE *buff, LBA_t sector, UINT count) {
    if (!count || sector>=sectors || count>sectors-sector) return RES_PARERR;
    if (disk_status(drive)) return RES_NOTRDY;
    io_failed=0;
    for (unsigned i=0; i<count; ++i) {
        uint32_t arg=high_capacity ? sector+i : (sector+i)*512;
        if (command(24,arg)) goto fail;
        exchange(255); exchange(0xfe);
        const unsigned char *data=buff+i*512;
        for (unsigned j=0; j<512; ++j) exchange(data[j]);
        unsigned crc=crc16(data,512); exchange(crc>>8); exchange(crc&255);
        if ((exchange(255)&31)!=5 || !ready(1000) || io_failed) goto fail;
        if (command(13,0) || exchange(255)) goto fail;
        release();
    }
    return RES_OK;
fail:
    release(); initialized=0; return RES_ERROR;
}
DRESULT disk_ioctl(BYTE drive, BYTE cmd, void *buff) {
    if (disk_status(drive)) return RES_NOTRDY;
    if (cmd==CTRL_SYNC) { spi_select(0,1); int ok=ready(1000); release(); return ok ? RES_OK : RES_ERROR; }
    if (cmd==GET_SECTOR_COUNT) { *(LBA_t *)buff=sectors; return RES_OK; }
    if (cmd==GET_SECTOR_SIZE) { *(WORD *)buff=512; return RES_OK; }
    return RES_PARERR;
}

void sd_get_info(hal_sd_info_t *info) {
    *info=(hal_sd_info_t){sectors,initialized?6000000u:400000u,1,0,!(sd_detect_in_read()&1u),!disk_status(0),0,0,0};
}
