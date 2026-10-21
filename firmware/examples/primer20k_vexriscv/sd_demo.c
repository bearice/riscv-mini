/* UART-loaded SD acceptance tool. It is not linked into the base monitor. */
#include <hal/hal.h>
#include "ff.h"
#include <string.h>
static uint8_t buffer[8193]; /* +1 permits an intentionally unaligned DMA caller. */
static uint32_t crc_update(uint32_t crc,const uint8_t *data,unsigned count) {
    for(unsigned i=0;i<count;++i) {
        crc^=data[i];for(unsigned b=0;b<8;++b)crc=(crc>>1)^((0u-(crc&1u))&0xedb88320u);
    }
    return crc;
}
static uint8_t pattern(unsigned offset) {return (offset*17u)^(offset>>8)^0x5au;}
static void status(void) {
    hal_sd_info_t info;hal_sd_get_info(&info);
    hal_uart_puts("SD native=");hal_uart_hex(info.native);hal_uart_puts(" width=");hal_uart_hex(info.bus_width);
    hal_uart_puts(" hz=");hal_uart_hex(info.clock_hz);hal_uart_puts(" ready=");hal_uart_hex(info.initialized);
    hal_uart_puts(" present=");hal_uart_hex(info.present);hal_uart_puts(" reads=");hal_uart_hex(info.read_blocks);
    hal_uart_puts(" writes=");hal_uart_hex(info.written_blocks);hal_uart_puts(" errors=");hal_uart_hex(info.errors);hal_uart_puts("\r\n");
    hal_video_status();hal_stats_t stats;hal_get_stats(&stats);
    hal_uart_puts("IRQ drops=");hal_uart_hex(stats.uart_drops);hal_uart_puts(" unhandled=");hal_uart_hex(stats.unhandled_irqs);hal_uart_puts("\r\n");
}
static void file_read(void) {
    if(hal_sd_mount()!=HAL_OK)goto fail;
    FIL file;if(f_open(&file,"RVTEST00.BIN",FA_READ)!=FR_OK)goto fail;
    unsigned size=0;uint32_t crc=~0u;UINT n;
    for(;;) {
        if(f_read(&file,buffer+1,8192,&n)!=FR_OK) {f_close(&file);goto fail;}
        if(!n)break;
        crc=crc_update(crc,buffer+1,n);size+=n;
    }
    if(f_close(&file)!=FR_OK)goto fail;
    hal_uart_puts("SD READ PASS bytes=");hal_uart_hex(size);hal_uart_puts(" crc=");hal_uart_hex(~crc);hal_uart_puts("\r\n");return;
fail:hal_uart_puts("SD READ FAIL\r\n");
}
static void file_write(void) {
    if(hal_sd_mount()!=HAL_OK)goto fail;
    FIL file;char name[]="RV6T0000.BIN";FRESULT result=FR_EXIST;
    for(unsigned number=0;number<100 && result==FR_EXIST;++number) {
        name[6]='0'+number/10;name[7]='0'+number%10;
        result=f_open(&file,name,FA_WRITE|FA_CREATE_NEW);
    }
    if(result!=FR_OK)goto fail;
    uint32_t expected=~0u;UINT n;
    for(unsigned offset=0;offset<65536;offset+=8192) {
        for(unsigned i=0;i<8192;++i)buffer[i+1]=pattern(offset+i);
        expected=crc_update(expected,buffer+1,8192);
        if(f_write(&file,buffer+1,8192,&n)!=FR_OK || n!=8192) {f_close(&file);goto fail;}
    }
    if(f_sync(&file)!=FR_OK) {f_close(&file);goto fail;}
    if(f_close(&file)!=FR_OK || f_open(&file,name,FA_READ)!=FR_OK)goto fail;
    uint32_t actual=~0u;unsigned offset=0;
    for(;;) {
        if(f_read(&file,buffer+1,8192,&n)!=FR_OK) {f_close(&file);goto fail;}
        if(!n)break;
        for(unsigned i=0;i<n;++i)if(buffer[i+1]!=pattern(offset+i)) {f_close(&file);goto fail;}
        actual=crc_update(actual,buffer+1,n);offset+=n;
    }
    if(f_close(&file)!=FR_OK || offset!=65536 || actual!=expected)goto fail;
    hal_uart_puts("SD WRITE PASS file=");hal_uart_puts(name);hal_uart_puts(" bytes=00010000 crc=");hal_uart_hex(~actual);hal_uart_puts("\r\n");return;
fail:hal_uart_puts("SD WRITE FAIL\r\n");
}
static void block_read(void) {
    if(hal_sd_mount()!=HAL_OK)goto fail;
    /* Same data through 16 one-sector requests and one 16-sector request;
       transfers exceed the driver's 8-sector bounce chunk and are unaligned. */
    uint32_t singles=~0u;
    for(unsigned sector=0;sector<16;++sector) {
        if(hal_sd_read(sector,buffer+1,1)!=HAL_OK)goto fail;
        singles=crc_update(singles,buffer+1,512);
    }
    if(hal_sd_read(0,buffer+1,16)!=HAL_OK || crc_update(~0u,buffer+1,8192)!=singles)goto fail;
    hal_uart_puts("SD BLOCK PASS single/multiple/unaligned CRC=");hal_uart_hex(~singles);hal_uart_puts("\r\n");return;
fail:hal_uart_puts("SD BLOCK FAIL\r\n");
}
static unsigned frame;
static void swap_frame(void) {
    frame^=1u;volatile uint16_t *pixels=hal_video_frame(frame);
    for(unsigned y=0;y<272;++y)for(unsigned x=0;x<480;++x) {
        uint16_t color=x<160?0xf800:x<320?0x07e0:0x001f;
        if(y>=224)color=((x/16)&31)*0x0841u;
        if(!x || x==479 || !y || y==271)color=0xffff;
        if((x<16 && y<16) || (x>=464 && y>=256))color=frame?0xffe0:0x07ff;
        pixels[y*480+x]=color;
    }
    hal_uart_puts(hal_video_present(frame)==HAL_OK?"FRAME PASS\r\n":"FRAME FAIL\r\n");
}
int main(void) {
    hal_init();unsigned ready=hal_sd_mount()==HAL_OK;
    hal_spi_lcd_show(ready);hal_video_init();
    hal_uart_puts("SD DEMO: r=existing-file CRC, w=new-file write/read, b=blocks, f=frame, s=status, !=reboot\r\n");
    status();hal_uart_puts("SYSTEM READY\r\n> ");
    for(;;) {
        hal_poll();int ch=hal_uart_getc();if(ch<0 || ch=='\r' || ch=='\n')continue;
        if(ch=='!')hal_reboot();
        if(ch=='r')file_read();else if(ch=='w')file_write();else if(ch=='b')block_read();
        else if(ch=='f')swap_frame();else if(ch=='s')status();
        hal_uart_puts("> ");
    }
}
