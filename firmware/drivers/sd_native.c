/* Native SD memory protocol, four-bit SDR; DMA uses an aligned DDR bounce buffer.
 * FatFs owns writes. No raw-sector write command is exposed by the monitor.
 */
#include "io.h"
#include "ff.h"
#include "diskio.h"
#include <hal/hal.h>
#include <generated/csr.h>
#include <string.h>

enum {RESP_NONE=0,RESP_SHORT=1,RESP_LONG=2,RESP_BUSY=3,RESP_CRC=4,DATA_READ=32,DATA_WRITE=64};
static unsigned initialized,high_capacity,rca,width,clock_hz=400000;
/* Experimental read clocks are selected by HAL; initialization restores this
 * qualified default. Keep the independently accepted write clock unchanged. */
#define SD_READ_CLOCK_HZ 15000000u
#define SD_WRITE_CLOCK_HZ 7500000u
static unsigned read_clock_hz=SD_READ_CLOCK_HZ,high_speed,switch_supported;
static uint32_t sectors,read_blocks,written_blocks,errors;
static _Alignas(4) uint8_t bounce[4096];
static unsigned present(void) {return !(sdcard_phy_card_detect_read()&1u);}
static uint32_t response(unsigned word) {return sdcard_core_cmd_response_read_word(3-word);}
static void reset_controller(void) {
    sdcard_block2mem_dma_enable_write(0);sdcard_mem2block_dma_enable_write(0);
    sd_control_reset_write(1);io_delay_ms(1);sd_control_reset_write(0);io_delay_ms(1);
    sdcard_ev_enable_write(0);sdcard_ev_pending_write(15);
    sdcard_phy_clocker_divider_write(150);sdcard_phy_settings_write(0);
    sdcard_phy_cmdr_timeout_write(CONFIG_CLOCK_FREQUENCY/4u);
    sdcard_phy_datar_timeout_write(CONFIG_CLOCK_FREQUENCY/2u);
    width=1;clock_hz=400000;high_speed=0;
}
static void set_clock(unsigned hz) {
    if(clock_hz==hz)return;
    sdcard_phy_clocker_divider_write(CONFIG_CLOCK_FREQUENCY/hz);clock_hz=hz;
    io_delay_ms(1); /* Finish old half-period before issuing another command. */
}
static int wait_event(unsigned data) {
    uint32_t start=io_ticks();
    do {
        unsigned event=data?sdcard_core_data_event_read():sdcard_core_cmd_event_read();
        if(event&1u)return !(event&14u);
        if(!present())return 0;
    } while((uint32_t)(io_ticks()-start)<CONFIG_CLOCK_FREQUENCY);
    return 0;
}
static int command(unsigned cmd,uint32_t arg,unsigned flags) {
    if(!present())return 0;
    sdcard_core_cmd_argument_write(arg);sdcard_core_cmd_command_write((cmd<<8)|flags);
    sdcard_core_cmd_send_write(1);
    return wait_event(0);
}
static int checked_command(unsigned cmd,uint32_t arg,unsigned flags) {
    return command(cmd,arg,flags) && !(response(0)&0xfff9a008u); /* R1 error bits only. */
}
static int app_command(unsigned cmd,uint32_t arg) {
    return checked_command(55,rca<<16,RESP_SHORT|RESP_CRC) && checked_command(cmd,arg,RESP_SHORT|RESP_CRC);
}
static int wait_dma(unsigned write) {
    uint32_t start=io_ticks();
    do {
        if(write?sdcard_mem2block_dma_error_read():sdcard_block2mem_dma_error_read())return 0;
        if(write?sdcard_mem2block_dma_done_read():sdcard_block2mem_dma_done_read()) {
            __asm__ volatile("fence rw,rw":::"memory");
#if MINI_CPU_DCACHE
            if(!write)__asm__ volatile(".word 0x0000500f":::"memory");
#endif
            return 1;
        }
        if(!present())return 0;
    } while((uint32_t)(io_ticks()-start)<CONFIG_CLOCK_FREQUENCY);
    return 0;
}
static void start_dma(unsigned write,unsigned bytes) {
    __asm__ volatile("fence rw,rw":::"memory");
    if(write) {
        sdcard_mem2block_dma_enable_write(0);sdcard_mem2block_dma_base_write((uintptr_t)bounce);
        sdcard_mem2block_dma_length_write(bytes);sdcard_mem2block_dma_enable_write(1);
    } else {
        sdcard_block2mem_dma_enable_write(0);sdcard_block2mem_dma_base_write((uintptr_t)bounce);
        sdcard_block2mem_dma_length_write(bytes);sdcard_block2mem_dma_enable_write(1);
    }
}
static void stop_dma(void) {sdcard_block2mem_dma_enable_write(0);sdcard_mem2block_dma_enable_write(0);}
static int switch_function(uint32_t argument) {
    sdcard_core_block_length_write(64);sdcard_core_block_count_write(1);start_dma(0,64);
    int ok=checked_command(6,argument,RESP_SHORT|RESP_CRC|DATA_READ) && wait_event(1) && wait_dma(0);
    stop_dma();return ok;
}
hal_result_t sd_set_read_clock(unsigned hz) {
    if(hz!=7500000u && hz!=10000000u && hz!=15000000u && hz!=30000000u)return HAL_INVALID;
    if(!initialized || !present())return HAL_NO_MEDIA;
    if(hz>25000000u && !high_speed) {
        if(!switch_supported)return HAL_UNSUPPORTED;
        set_clock(SD_WRITE_CLOCK_HZ);
        /* Group1 function1=HighSpeed; all other groups F=unchanged. The 64B
         * status is MSB-first: support at byte13, selected function at byte16. */
        if(!switch_function(0x00fffff1u))goto fail;
        if(!(bounce[13]&2u))return HAL_UNSUPPORTED;
        if(!switch_function(0x80fffff1u))goto fail;
        if((bounce[16]&15u)!=1u)return HAL_UNSUPPORTED;
        high_speed=1;io_delay_ms(1);
    }
    read_clock_hz=hz;set_clock(hz);return HAL_OK;
fail:
    ++errors;initialized=0;reset_controller();return HAL_IO;
}
static unsigned csd_bits(unsigned low,unsigned count) {
    unsigned word=low/32,shift=low%32;uint64_t value=response(word);
    if(shift+count>32)value|=(uint64_t)response(word+1)<<32;
    return (value>>shift)&((1u<<count)-1);
}
DSTATUS disk_initialize(BYTE drive) {
    if(drive)return STA_NOINIT;
    initialized=0;sectors=0;rca=0;high_capacity=0;reset_controller();
    if(!present())goto fail;
    sdcard_phy_init_initialize_write(1);io_delay_ms(1);
    if(!command(0,0,RESP_NONE))goto fail;
    io_delay_ms(1);
    unsigned v2=command(8,0x1aa,RESP_SHORT|RESP_CRC);
    if(v2 && (response(0)&0xfffu)!=0x1aa)goto fail;
    uint32_t start=io_ticks(),ocr=0;
    do {
        if(!checked_command(55,0,RESP_SHORT|RESP_CRC) || !command(41,0x00ff8000u|(v2?0x40000000u:0),RESP_SHORT))goto fail;
        ocr=response(0);if(ocr&0x80000000u)break;
        io_delay_ms(10);
    } while((uint32_t)(io_ticks()-start)<2u*CONFIG_CLOCK_FREQUENCY);
    if(!(ocr&0x80000000u) || !(ocr&0x00ff8000u))goto fail;
    high_capacity=!!(ocr&0x40000000u);
    if(!command(2,0,RESP_LONG|RESP_CRC) || !command(3,0,RESP_SHORT|RESP_CRC))goto fail;
    if(response(0)&0xe000u)goto fail; /* R6 error bits, not R1/RCA. */
    rca=response(0)>>16;if(!rca || !command(9,rca<<16,RESP_LONG|RESP_CRC))goto fail;
    switch_supported=!!(csd_bits(84,12)&(1u<<10));
    unsigned version=csd_bits(126,2);
    if(version==1) {
        unsigned size=csd_bits(48,22);if(size==0x3fffffu)goto fail;
        sectors=(size+1u)*1024u;
    } else if(version==0) {
        unsigned shift=csd_bits(80,4)+csd_bits(47,3)+2;
        uint64_t total=(uint64_t)(csd_bits(62,12)+1u)<<shift;
        total>>=9;if(!total || total>0xffffffffu)goto fail;sectors=total;
    } else goto fail;
    if(!checked_command(7,rca<<16,RESP_BUSY|RESP_CRC))goto fail;
    if(!high_capacity && !checked_command(16,512,RESP_SHORT|RESP_CRC))goto fail;
    /* SCR is read in one-bit mode before negotiating four-bit operation. */
    if(!checked_command(55,rca<<16,RESP_SHORT|RESP_CRC))goto fail;
    sdcard_core_block_length_write(8);sdcard_core_block_count_write(1);start_dma(0,8);
    if(!checked_command(51,0,RESP_SHORT|RESP_CRC|DATA_READ) || !wait_event(1) || !wait_dma(0))goto fail;
    stop_dma();
    if(!(bounce[1]&4) || !app_command(6,2))goto fail;
    sdcard_phy_settings_write(1);width=4;
    set_clock(SD_WRITE_CLOCK_HZ);initialized=1;
    if(sd_set_read_clock(SD_READ_CLOCK_HZ)!=HAL_OK)goto fail;
    puts_uart("SD init PASS: native4 DMA sectors=");io_hex(sectors);
    puts_uart(" SDHC=");io_hex(high_capacity);puts_uart(" hz=");io_hex(clock_hz);puts_uart("\r\n");
    return 0;
fail:
    ++errors;initialized=0;reset_controller();
    puts_uart("SD unavailable: native timeout/protocol/no-media; UART remains available\r\n");
    return present()?STA_NOINIT:STA_NOINIT|STA_NODISK;
}
DSTATUS disk_status(BYTE drive) {
    if(drive)return STA_NOINIT;
    if(!present()) {initialized=0;return STA_NOINIT|STA_NODISK;}
    return initialized?0:STA_NOINIT;
}
static DRESULT transfer(BYTE drive,void *buffer,LBA_t sector,UINT count,unsigned write) {
    if(drive || !buffer || !count)return RES_PARERR;
    if(disk_status(drive))return RES_NOTRDY;
    if(sector>=sectors || count>sectors-sector)return RES_PARERR;
    set_clock(write?SD_WRITE_CLOCK_HZ:read_clock_hz);
    uint8_t *data=buffer;
    while(count) {
        unsigned blocks=count>8?8:count,bytes=blocks*512;
        if(write)memcpy(bounce,data,bytes);
        sdcard_core_block_length_write(512);sdcard_core_block_count_write(blocks);
        start_dma(write,bytes);
        unsigned cmd=write?(blocks==1?24:25):(blocks==1?17:18);
        unsigned arg=high_capacity?sector:sector*512;
        if(!checked_command(cmd,arg,RESP_SHORT|RESP_CRC|(write?DATA_WRITE:DATA_READ)) || !wait_event(1) || !wait_dma(write))goto fail;
        stop_dma();
        if(blocks>1 && !checked_command(12,0,RESP_BUSY|RESP_CRC))goto fail;
        if(write && !checked_command(13,rca<<16,RESP_SHORT|RESP_CRC))goto fail;
        if(write)written_blocks+=blocks;else {memcpy(data,bounce,bytes);read_blocks+=blocks;}
        data+=bytes;sector+=blocks;count-=blocks;
    }
    return RES_OK;
fail:
    puts_uart("SD transfer FAIL cmd_event=");io_hex(sdcard_core_cmd_event_read());
    puts_uart(" data_event=");io_hex(sdcard_core_data_event_read());
    puts_uart(" dma_error=");io_hex(write?sdcard_mem2block_dma_error_read():sdcard_block2mem_dma_error_read());
    puts_uart(" response=");io_hex(response(0));puts_uart("\r\n");
    ++errors;initialized=0;reset_controller();return present()?RES_ERROR:RES_NOTRDY;
}
DRESULT disk_read(BYTE drive,BYTE *buffer,LBA_t sector,UINT count) {return transfer(drive,buffer,sector,count,0);}
DRESULT disk_write(BYTE drive,const BYTE *buffer,LBA_t sector,UINT count) {return transfer(drive,(void *)buffer,sector,count,1);}
DRESULT disk_ioctl(BYTE drive,BYTE cmd,void *buffer) {
    if(disk_status(drive))return RES_NOTRDY;
    if(cmd==CTRL_SYNC) {
        if(checked_command(13,rca<<16,RESP_SHORT|RESP_CRC))return RES_OK;
        ++errors;initialized=0;reset_controller();return RES_ERROR;
    }
    if(!buffer)return RES_PARERR;
    if(cmd==GET_SECTOR_COUNT) {*(LBA_t *)buffer=sectors;return RES_OK;}
    if(cmd==GET_SECTOR_SIZE) {*(WORD *)buffer=512;return RES_OK;}
    if(cmd==GET_BLOCK_SIZE) {*(DWORD *)buffer=1;return RES_OK;}
    return RES_PARERR;
}
void sd_get_info(hal_sd_info_t *info) {
    *info=(hal_sd_info_t){sectors,clock_hz,width,1,present(),!disk_status(0),read_blocks,written_blocks,errors,
        read_clock_hz,SD_WRITE_CLOCK_HZ,high_speed};
}
