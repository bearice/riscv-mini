/* Independent Ethernet acceptance app; base monitor has no IP stack. */
#include <hal/hal.h>
#include "ff.h"
#include <string.h>
#include "../common/packet_echo.h"
static hal_usb_report_t last_report;
static void usb_poll(void) {
    hal_usb_poll();hal_usb_key_t key;
    while(hal_usb_key_take(&key)==HAL_OK) {
        hal_uart_puts(key.pressed?"USB KEY DOWN usage=":"USB KEY UP usage=");hal_uart_hex(key.usage);
        hal_uart_puts(" modifiers=");hal_uart_hex(key.modifiers);hal_uart_puts(" itf=");hal_uart_hex(key.interface);hal_uart_puts("\r\n");
    }
    hal_usb_report_t report;while(hal_usb_report_take(&report)==HAL_OK)last_report=report;
    hal_usb_mouse_t mouse;while(hal_usb_mouse_take(&mouse)==HAL_OK) {}
}
static void usb_status(void) {
    hal_usb_info_t u;hal_usb_get_info(&u);
    hal_uart_puts("USB ready=");hal_uart_hex(u.initialized);hal_uart_puts(" phy=");hal_uart_hex(u.phy_id);
    hal_uart_puts(" phy_ready=");hal_uart_hex(u.phy_ready);hal_uart_puts(" phy_error=");hal_uart_hex(u.phy_error);
    hal_uart_puts(" lines=");hal_uart_hex(u.lines);hal_uart_puts(" connected=");hal_uart_hex(u.connected);
    hal_uart_puts(" vid=");hal_uart_hex(u.vid);hal_uart_puts(" pid=");hal_uart_hex(u.pid);hal_uart_puts(" speed=");hal_uart_hex(u.speed);
    hal_uart_puts(" hid=");hal_uart_hex(u.hid_interfaces);hal_uart_puts(" reports=");hal_uart_hex(u.reports);
    hal_uart_puts(" keys=");hal_uart_hex(u.key_events);hal_uart_puts(" key_drops=");hal_uart_hex(u.key_drops);
    hal_uart_puts(" report_drops=");hal_uart_hex(u.report_drops);hal_uart_puts(" errors=");hal_uart_hex(u.errors);
    hal_uart_puts(" irqs=");hal_uart_hex(u.irqs);hal_uart_puts(" frame=");hal_uart_hex(u.frame);hal_uart_puts(" control=");hal_uart_hex(u.control);
    hal_uart_puts(" port=");hal_uart_hex(u.port_status);hal_uart_puts(" hcca=");hal_uart_hex(u.hcca);hal_uart_puts("\r\n");
    hal_uart_puts("USB LAST itf=");hal_uart_hex(last_report.interface);hal_uart_puts(" length=");hal_uart_hex(last_report.length);
    for(unsigned i=0;i<last_report.length;++i) {hal_uart_putc(' ');hal_uart_hex(last_report.data[i]);}hal_uart_puts("\r\n");
}
static void network_poll(void);
static void ethernet_status(void);
static void fill(void);
static uint8_t packet[1514];
static unsigned pending_reply,arp_replies,icmp_replies,udp_replies,discarded;
static void ethernet_status(void) {
    hal_eth_info_t e;hal_eth_get_info(&e);
    hal_uart_puts("ETH ready=");hal_uart_hex(e.initialized);hal_uart_puts(" phy=");hal_uart_hex(e.phy_id);
    network_poll();
    hal_uart_puts(" addr=");hal_uart_hex(e.phy_address);hal_uart_puts(" link=");hal_uart_hex(e.link);
    network_poll();
    hal_uart_puts(" mbps=");hal_uart_hex(e.speed_mbps);hal_uart_puts(" full=");hal_uart_hex(e.full_duplex);
    network_poll();
    hal_uart_puts(" bmcr=");hal_uart_hex(e.bmcr);hal_uart_puts(" bmsr=");hal_uart_hex(e.bmsr);
    network_poll();
    hal_uart_puts(" partner=");hal_uart_hex(e.partner);hal_uart_puts(" rmii=");hal_uart_hex(e.rmii);
    network_poll();
    hal_uart_puts(" ref_hz=");hal_uart_hex(e.ref_clock_hz);hal_uart_puts(" rx=");hal_uart_hex(e.rx_frames);
    network_poll();
    hal_uart_puts(" tx=");hal_uart_hex(e.tx_frames);hal_uart_puts(" drops=");hal_uart_hex(e.rx_drops);
    network_poll();
    hal_uart_puts(" crc=");hal_uart_hex(e.crc_errors);hal_uart_puts(" preamble=");hal_uart_hex(e.preamble_errors);
    network_poll();
    hal_uart_puts(" irq=");hal_uart_hex(e.irqs);hal_uart_puts(" mdio=");hal_uart_hex(e.mdio_errors);
    network_poll();
    hal_uart_puts(" changes=");hal_uart_hex(e.link_changes);hal_uart_puts(" busy=");hal_uart_hex(e.tx_busy);
    network_poll();
    hal_uart_puts(" arp=");hal_uart_hex(arp_replies);hal_uart_puts(" icmp=");hal_uart_hex(icmp_replies);
    network_poll();
    hal_uart_puts(" udp=");hal_uart_hex(udp_replies);hal_uart_puts(" ignored=");hal_uart_hex(discarded);hal_uart_puts("\r\n");
    network_poll();
}
static void network_poll(void) {
    /* Drain both RX slots and transmit their replies before returning to
       USB/audio work. Bound the loop even under continuous background traffic. */
    for(unsigned budget=0;budget<4;++budget) {
        if(pending_reply) {
            hal_result_t result=hal_eth_send(packet,pending_reply);
            if(result==HAL_BUSY || result==HAL_NO_MEDIA)return;
            pending_reply=0;if(result!=HAL_OK)++discarded;
        }
        unsigned size;if(hal_eth_receive(packet,sizeof(packet),&size)!=HAL_OK)return;
        unsigned type=net16(packet+12),protocol=(size>=34 && type==0x0800)?packet[23]:0;
        pending_reply=packet_reply(packet,size);
        if(!pending_reply) {++discarded;continue;}
        if(type==0x0806)++arp_replies;else if(protocol==1)++icmp_replies;else ++udp_replies;
    }
}
static uint32_t ring[32768],pcm[256];
static unsigned phase,streaming,frame;
static const int16_t sine[32]={0,200,392,569,724,851,946,1004,1024,1004,946,851,724,569,392,200,
    0,-200,-392,-569,-724,-851,-946,-1004,-1024,-1004,-946,-851,-724,-569,-392,-200};
static uint32_t next_pcm(unsigned offset) {
    /* Low amplitude: left 46875/64 Hz, right 46875/96 Hz, independent phases. */
    int16_t left=sine[(offset/2)&31],right=sine[(offset/3)&31];
    return (uint16_t)left|((uint32_t)(uint16_t)right<<16);
}
static void fill(void) {
    network_poll();usb_poll();if(!streaming)return;
    /* phase is the ring producer count. Generate only the newly freed space;
       avoid preparing 256 samples on every poll when the ring is already full. */
    hal_audio_info_t audio;hal_audio_get_info(&audio);
    unsigned used=phase-audio.fetched;
    if(used>32768) {streaming=0;hal_uart_puts("AUDIO REFILL FAIL\r\n");return;}
    unsigned count=32768-used;if(count>256)count=256;
    if(!count)return;
    for(unsigned i=0;i<count;++i)pcm[i]=next_pcm(phase+i);
    unsigned written=0;hal_result_t result=hal_audio_ring_write(pcm,count,&written);
    phase+=written;
    if(result!=HAL_OK && result!=HAL_BUSY) {streaming=0;hal_uart_puts("AUDIO REFILL FAIL\r\n");}
}
static void status(void) {
    hal_audio_info_t a;hal_audio_get_info(&a);
    hal_uart_puts("AUDIO hz=");hal_uart_hex(a.sample_rate);hal_uart_puts(" control=");hal_uart_hex(a.control);
    fill();
    hal_uart_puts(" level=");hal_uart_hex(a.level);hal_uart_puts(" frames=");hal_uart_hex(a.frames);
    fill();
    hal_uart_puts(" played=");hal_uart_hex(a.played);hal_uart_puts(" underruns=");hal_uart_hex(a.underruns);
    fill();
    hal_uart_puts(" overruns=");hal_uart_hex(a.overruns);hal_uart_puts(" fetched=");hal_uart_hex(a.fetched);
    fill();
    hal_uart_puts(" wraps=");hal_uart_hex(a.wraps);hal_uart_puts(" errors=");hal_uart_hex(a.errors);
    fill();
    hal_uart_puts(" busy=");hal_uart_hex(a.busy);hal_uart_puts(" amp=");hal_uart_hex(a.amplifier);hal_uart_puts("\r\n");
    fill();
    ethernet_status();hal_video_status();hal_sd_info_t sd;hal_sd_get_info(&sd);
    hal_uart_puts("SD ready=");hal_uart_hex(sd.initialized);hal_uart_puts(" reads=");hal_uart_hex(sd.read_blocks);
    fill();
    hal_uart_puts(" errors=");hal_uart_hex(sd.errors);hal_stats_t irq;hal_get_stats(&irq);
    fill();
    hal_uart_puts(" drops=");hal_uart_hex(irq.uart_drops);hal_uart_puts(" unhandled=");hal_uart_hex(irq.unhandled_irqs);hal_uart_puts("\r\n");
    fill();
}
static void begin(void) {
    streaming=0;phase=0;
    if(hal_audio_ring_begin(ring,32768)!=HAL_OK)goto fail;
    for(unsigned offset=0;offset<32768;offset+=256) {
        for(unsigned i=0;i<256;++i)pcm[i]=next_pcm(phase+i);
        unsigned written=0;
        if(hal_audio_ring_write(pcm,256,&written)!=HAL_OK || written!=256)goto fail;
        phase+=written;hal_poll();network_poll();
    }
    uint32_t start=hal_time_ms();
    while(hal_time_ms()-start<100) {
        hal_poll();network_poll();
        hal_audio_info_t a;hal_audio_get_info(&a);if(a.level>=256)break;
    }
    if(hal_audio_start()!=HAL_OK)goto fail;
    streaming=1;hal_uart_puts("AUDIO DMA START PASS muted\r\n");return;
fail:hal_audio_stop();hal_uart_puts("AUDIO DMA START FAIL\r\n");
}
static void file_read(void) {
    FIL file;UINT n;unsigned bytes=0;uint32_t crc=~0u;
    static uint8_t data[4096];
    if(f_open(&file,"RVTEST00.BIN",FA_READ)!=FR_OK)goto fail;
    for(;;) {
        if(f_read(&file,data,sizeof(data),&n)!=FR_OK) {f_close(&file);goto fail;}
        if(!n)break;
        for(unsigned i=0;i<n;++i) {
            if(!(i&127))network_poll();
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
        if(!(y&3))fill();
    }
    hal_uart_puts(hal_video_present(frame)==HAL_OK?"FRAME PASS\r\n":"FRAME FAIL\r\n");
}

int main(void) {
    hal_init();
    if(hal_eth_get_mac(echo_mac)!=HAL_OK || (echo_mac[0]&3)!=2) {
        hal_uart_puts("FLASH UID/MAC FAIL\r\n");for(;;)hal_poll();
    }
    hal_eth_stop(); /* Avoid filling packet slots while displays initialize. */
    if(!packet_check(packet)) {hal_uart_puts("NETWORK PARSER FAIL\r\n");for(;;)hal_poll();}
    hal_uart_puts("NETWORK PARSER PASS: checksums, odd/MTU lengths, truncation, fragments, ARP\r\n");
    unsigned ready=hal_sd_mount()==HAL_OK;hal_spi_lcd_show(ready);hal_spi_lcd_network(echo_mac,echo_ip);hal_video_init();
    hal_eth_init();
    hal_uart_puts("ETHERNET DEMO IP=169.254.20.20 MAC=");
    static const char hex[]="0123456789abcdef";
    char address[18];for(unsigned i=0;i<6;++i) {address[3*i]=hex[echo_mac[i]>>4];address[3*i+1]=hex[echo_mac[i]&15];address[3*i+2]=':';}
    address[17]=0;hal_uart_puts(address);hal_uart_puts(" UDP=1234\r\n");
    hal_eth_info_t identity;hal_eth_get_info(&identity);
    hal_uart_puts("FLASH UID=");char uid[33];
    for(unsigned i=0;i<identity.uid_length;++i) {uid[2*i]=hex[identity.flash_uid[i]>>4];uid[2*i+1]=hex[identity.flash_uid[i]&15];}
    uid[2*identity.uid_length]=0;hal_uart_puts(uid);hal_uart_puts("\r\n");
    ethernet_status();
    usb_status();
    hal_uart_puts("SYSTEM READY d=muted audio DMA x=stop s=status r=SD CRC f=frame e=shared PHY reset u=USB status j=USB restart !=reboot\r\n> ");
    unsigned last_link=0;
    for(;;) {
        hal_poll();fill();
        hal_eth_info_t link;hal_eth_get_info(&link);
        if(link.link!=last_link) {hal_spi_lcd_link(link.link);last_link=link.link;}
        int ch=hal_uart_getc();
        if(ch<0 || ch=='\r' || ch=='\n')continue;
        if(ch=='!')hal_reboot();
        if(ch=='d')begin();else if(ch=='s')status();else if(ch=='r')file_read();else if(ch=='f')swap_frame();
        else if(ch=='x') {streaming=0;hal_uart_puts(hal_audio_stop()==HAL_OK?"AUDIO STOP PASS\r\n":"AUDIO STOP FAIL\r\n");}
        else if(ch=='e') {pending_reply=0;hal_phys_reset(10);hal_uart_puts("PHY RESET DONE\r\n");ethernet_status();}
        else if(ch=='u')usb_status();
        else if(ch=='j') {hal_uart_puts(hal_usb_init()==HAL_OK?"USB RESTART PASS\r\n":"USB RESTART FAIL\r\n");usb_status();}
        hal_uart_puts("> ");
    }
}
