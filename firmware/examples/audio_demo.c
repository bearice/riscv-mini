/* Independent, initially muted audio acceptance app; base monitor stays small. */
#include <hal/hal.h>
#include "ff.h"
#include <string.h>
static uint32_t ring[32768],pcm[256];
static unsigned phase,streaming,frame,audible_until;
static const int16_t sine[32]={0,200,392,569,724,851,946,1004,1024,1004,946,851,724,569,392,200,
    0,-200,-392,-569,-724,-851,-946,-1004,-1024,-1004,-946,-851,-724,-569,-392,-200};
static uint32_t next_pcm(unsigned offset) {
    /* Low amplitude: left 46875/64 Hz, right 46875/96 Hz, independent phases. */
    int16_t left=sine[(offset/2)&31],right=sine[(offset/3)&31];
    return (uint16_t)left|((uint32_t)(uint16_t)right<<16);
}
static void fill(void) {
    if(!streaming)return;
    for(unsigned i=0;i<256;++i)pcm[i]=next_pcm(phase+i);
    unsigned written=0;hal_result_t result=hal_audio_ring_write(pcm,256,&written);
    phase+=written;
    if(result!=HAL_OK && result!=HAL_BUSY) {streaming=0;hal_uart_puts("AUDIO REFILL FAIL\r\n");}
}
static void status(void) {
    hal_audio_info_t a;hal_audio_get_info(&a);
    hal_uart_puts("AUDIO hz=");hal_uart_hex(a.sample_rate);hal_uart_puts(" control=");hal_uart_hex(a.control);
    hal_uart_puts(" level=");hal_uart_hex(a.level);hal_uart_puts(" frames=");hal_uart_hex(a.frames);
    hal_uart_puts(" played=");hal_uart_hex(a.played);hal_uart_puts(" underruns=");hal_uart_hex(a.underruns);
    hal_uart_puts(" overruns=");hal_uart_hex(a.overruns);hal_uart_puts(" fetched=");hal_uart_hex(a.fetched);
    hal_uart_puts(" wraps=");hal_uart_hex(a.wraps);hal_uart_puts(" errors=");hal_uart_hex(a.errors);
    hal_uart_puts(" busy=");hal_uart_hex(a.busy);hal_uart_puts(" amp=");hal_uart_hex(a.amplifier);hal_uart_puts("\r\n");
    hal_video_status();hal_sd_info_t sd;hal_sd_get_info(&sd);
    hal_uart_puts("SD ready=");hal_uart_hex(sd.initialized);hal_uart_puts(" reads=");hal_uart_hex(sd.read_blocks);
    hal_uart_puts(" errors=");hal_uart_hex(sd.errors);hal_stats_t irq;hal_get_stats(&irq);
    hal_uart_puts(" drops=");hal_uart_hex(irq.uart_drops);hal_uart_puts(" unhandled=");hal_uart_hex(irq.unhandled_irqs);hal_uart_puts("\r\n");
}
static void begin(void) {
    streaming=0;phase=0;
    if(hal_audio_ring_begin(ring,32768)!=HAL_OK)goto fail;
    for(unsigned offset=0;offset<32768;offset+=256) {
        for(unsigned i=0;i<256;++i)pcm[i]=next_pcm(phase+i);
        unsigned written=0;
        if(hal_audio_ring_write(pcm,256,&written)!=HAL_OK || written!=256)goto fail;
        phase+=written;
    }
    uint32_t start=hal_time_ms();
    while(hal_time_ms()-start<100) {
        hal_audio_info_t a;hal_audio_get_info(&a);if(a.level>=256)break;
    }
    if(hal_audio_start()!=HAL_OK)goto fail;
    streaming=1;hal_uart_puts("AUDIO DMA START PASS muted\r\n");return;
fail:hal_audio_stop();hal_uart_puts("AUDIO DMA START FAIL\r\n");
}
static void pio_check(void) {
    streaming=0;
    if(hal_audio_stop()!=HAL_OK)goto fail;
    hal_audio_mute(1);
    for(unsigned i=0;i<256;++i)pcm[i]=next_pcm(i);
    unsigned written=0;
    if(hal_audio_write(pcm,256,&written)!=HAL_OK || written!=256 || hal_audio_start()!=HAL_OK)goto fail;
    hal_delay_ms(20);hal_audio_info_t a;hal_audio_get_info(&a);
    if(a.played!=256 || !a.underruns || a.overruns || a.errors || a.amplifier)goto fail;
    hal_uart_puts("AUDIO PIO/UNDERRUN PASS played=");hal_uart_hex(a.played);
    hal_uart_puts(" underruns=");hal_uart_hex(a.underruns);hal_uart_puts("\r\n");
    hal_audio_stop();return;
fail:hal_audio_stop();hal_uart_puts("AUDIO PIO FAIL\r\n");
}
static void file_read(void) {
    FIL file;UINT n;unsigned bytes=0;uint32_t crc=~0u;
    static uint8_t data[4096];
    if(f_open(&file,"RVTEST00.BIN",FA_READ)!=FR_OK)goto fail;
    for(;;) {
        if(f_read(&file,data,sizeof(data),&n)!=FR_OK) {f_close(&file);goto fail;}
        if(!n)break;
        for(unsigned i=0;i<n;++i) {
            crc^=data[i];for(unsigned b=0;b<8;++b)crc=(crc>>1)^((0u-(crc&1u))&0xedb88320u);
        }
        bytes+=n;fill();
    }
    if(f_close(&file)!=FR_OK)goto fail;
    hal_uart_puts("SD READ PASS bytes=");hal_uart_hex(bytes);hal_uart_puts(" crc=");hal_uart_hex(~crc);hal_uart_puts("\r\n");return;
fail:hal_uart_puts("SD READ FAIL\r\n");
}
static void swap_frame(void) {
    frame^=1;volatile uint16_t *pixels=hal_video_frame(frame);
    for(unsigned y=0;y<272;++y) {
        for(unsigned x=0;x<480;++x) {
            uint16_t color=x<160?0xf800:x<320?0x07e0:0x001f;
            if(y>=224)color=((x/16)&31)*0x0841u;
            if(!x || x==479 || !y || y==271)color=0xffff;
            if((x<16 && y<16) || (x>=464 && y>=256))color=frame?0xffe0:0x07ff;
            pixels[y*480+x]=color;
        }
        if(!(y&15))fill();
    }
    hal_uart_puts(hal_video_present(frame)==HAL_OK?"FRAME PASS\r\n":"FRAME FAIL\r\n");
}
int main(void) {
    hal_init();unsigned ready=hal_sd_mount()==HAL_OK;
    hal_spi_lcd_show(ready);hal_video_init();
    hal_uart_puts("AUDIO DEMO: d=muted DMA, t=PIO/underrun, p=pause, c=resume, x=stop, u=2s line-in tone, s=status, r=SD CRC, f=frame, !=reboot\r\n");
    hal_uart_puts("SYSTEM READY\r\n> ");
    for(;;) {
        hal_poll();fill();
        if(audible_until && hal_deadline_reached(hal_time_ms(),audible_until)) {hal_audio_mute(1);audible_until=0;}
        int ch=hal_uart_getc();if(ch<0 || ch=='\r' || ch=='\n')continue;
        if(ch=='!')hal_reboot();
        if(ch=='d')begin();else if(ch=='t')pio_check();else if(ch=='s')status();
        else if(ch=='u' && streaming) {hal_audio_mute(0);audible_until=hal_time_ms()+2000;hal_uart_puts("AUDIO LINE-IN 2s\r\n");}
        else if(ch=='p') {hal_audio_pause();hal_uart_puts("AUDIO PAUSE PASS\r\n");}
        else if(ch=='c')hal_uart_puts(hal_audio_start()==HAL_OK?"AUDIO RESUME PASS\r\n":"AUDIO RESUME FAIL\r\n");
        else if(ch=='x') {streaming=0;hal_uart_puts(hal_audio_stop()==HAL_OK?"AUDIO STOP PASS\r\n":"AUDIO STOP FAIL\r\n");}
        else if(ch=='r')file_read();else if(ch=='f')swap_frame();
        hal_uart_puts("> ");
    }
}
