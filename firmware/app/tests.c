/* On-board acceptance commands; all storage and code remain in DDR. */
#include "tests.h"
#include <hal/hal.h>
#include <generated/csr.h>
#include "ff.h"
#include <string.h>
#include "../examples/packet_echo.h"

#define RING_FRAMES 32768u
#define WORK_BYTES 8192u
#define PIXELS (480u*272u)
static uint32_t ring[RING_FRAMES],pcm[256],work[WORK_BYTES/4+1];
static uint8_t packet[HAL_ETH_MAX_FRAME];
static unsigned streaming,producer,network,pending_reply,network_replies,network_ignored;
static unsigned frame,audible_until,usb_input;
static volatile unsigned ecall_armed,ecalls;
static const int16_t sine[32]={0,200,392,569,724,851,946,1004,1024,1004,946,851,724,569,392,200,
    0,-200,-392,-569,-724,-851,-946,-1004,-1024,-1004,-946,-851,-724,-569,-392,-200};

static void result(const char *name,unsigned ok) {
    hal_uart_puts("TEST ");hal_uart_puts(name);hal_uart_puts(ok?" PASS\r\n":" FAIL\r\n");
}
static void value(const char *name,uint32_t number) {hal_uart_puts(name);hal_uart_hex(number);}
static uint32_t crc_update(uint32_t crc,const uint8_t *data,unsigned size) {
    for(unsigned i=0;i<size;++i) {
        crc^=data[i];for(unsigned b=0;b<8;++b)crc=(crc>>1)^((0u-(crc&1u))&0xedb88320u);
    }
    return crc;
}
static uint32_t crc_pixel(uint32_t crc,unsigned pixel) {
    /* Keep the two RGB565 bytes in registers instead of a DDR stack array. */
    for(unsigned byte=0;byte<2;++byte) {
        crc^=pixel&255u;pixel>>=8;
        for(unsigned bit=0;bit<8;++bit)crc=(crc>>1)^((0u-(crc&1u))&0xedb88320u);
    }
    return crc;
}
static uint32_t sample(unsigned offset) {
    int16_t left=sine[(offset/2)&31],right=sine[(offset/3)&31];
    return (uint16_t)left|((uint32_t)(uint16_t)right<<16);
}
void tests_poll(void) {
    hal_usb_key_t key;
    while(hal_usb_key_take(&key)==HAL_OK) {
        hal_uart_puts(key.pressed?"\r\nUSB KEY DOWN usage=":"\r\nUSB KEY UP usage=");hal_uart_hex(key.usage);
        value(" modifiers=",key.modifiers);hal_uart_puts("\r\n");
    }
    hal_usb_report_t report;
    while(hal_usb_report_take(&report)==HAL_OK)if(usb_input) {
        value("USB RAW device=",report.device);value(" interface=",report.interface);value(" bytes=",report.length);
        hal_uart_puts(" data=");static const char hex[]="0123456789ABCDEF";
        for(unsigned i=0;i<report.length;++i) {hal_uart_putc(hex[report.data[i]>>4]);hal_uart_putc(hex[report.data[i]&15]);}
        hal_uart_puts("\r\n");
    }
    hal_usb_mouse_t mouse;
    while(hal_usb_mouse_take(&mouse)==HAL_OK)if(usb_input) {
        value("USB MOUSE buttons=",mouse.buttons);value(" x=",(int32_t)mouse.x);
        value(" y=",(int32_t)mouse.y);value(" wheel=",(int32_t)mouse.wheel);hal_uart_puts("\r\n");
    }
    if(network)for(unsigned budget=0;budget<4;++budget) {
        if(pending_reply) {
            hal_result_t sent=hal_eth_send(packet,pending_reply);
            if(sent==HAL_BUSY || sent==HAL_NO_MEDIA)break;
            pending_reply=0;if(sent!=HAL_OK)++network_ignored;
        }
        unsigned size;if(hal_eth_receive(packet,sizeof(packet),&size)!=HAL_OK)break;
        pending_reply=packet_reply(packet,size);
        if(pending_reply)++network_replies;else ++network_ignored;
    }
    if(streaming) {
        hal_audio_info_t audio;hal_audio_get_info(&audio);
        unsigned used=producer-audio.fetched;
        if(used>RING_FRAMES) {streaming=0;hal_audio_mute(1);result("audio refill",0);return;}
        unsigned count=RING_FRAMES-used;if(count>1024)count=1024;
        while(count) {
            unsigned chunk=count>256?256:count;
            for(unsigned i=0;i<chunk;++i)pcm[i]=sample(producer+i);
            unsigned written;hal_result_t rc=hal_audio_ring_write(pcm,chunk,&written);producer+=written;
            if(rc!=HAL_OK && rc!=HAL_BUSY) {streaming=0;hal_audio_mute(1);result("audio refill",0);}
            if(!written || !streaming)break;
            count-=written;
        }
    }
    if(audible_until && hal_deadline_reached(hal_time_ms(),audible_until)) {
        hal_audio_mute(1);audible_until=0;
    }
}
static void cooperate(void) {tests_poll();hal_poll();}
static void wait_ms(unsigned ms) {
    uint32_t start=hal_time_ms();while(hal_time_ms()-start<ms)cooperate();
}
static unsigned audio_stop(void) {
    streaming=audible_until=0;hal_audio_mute(1);
    unsigned ok=hal_audio_stop()==HAL_OK;if(ok)hal_audio_mute(1);return ok;
}
static unsigned audio_begin(void) {
    if(!audio_stop() || hal_audio_ring_begin(ring,RING_FRAMES)!=HAL_OK)return 0;
    producer=0;
    for(unsigned i=0;i<RING_FRAMES;i+=256) {
        for(unsigned j=0;j<256;++j)pcm[j]=sample(producer+j);
        unsigned written;
        if(hal_audio_ring_write(pcm,256,&written)!=HAL_OK || written!=256) {audio_stop();return 0;}
        producer+=written;hal_poll();
    }
    wait_ms(20);
    if(hal_audio_start()!=HAL_OK) {audio_stop();return 0;}
    streaming=1;return 1;
}
static unsigned audio_ok(void) {
    hal_audio_info_t a;hal_audio_get_info(&a);
    return streaming && !a.underruns && !a.overruns && !a.errors && !a.amplifier;
}
static void audio_status(void) {
    hal_audio_info_t a;hal_audio_get_info(&a);
    value("AUDIO played=",a.played);value(" fetched=",a.fetched);value(" underruns=",a.underruns);
    value(" overruns=",a.overruns);value(" errors=",a.errors);value(" amp=",a.amplifier);hal_uart_puts("\r\n");
}
static unsigned audio_pio(void) {
    if(!audio_stop())return 0;
    hal_audio_mute(1);
    for(unsigned i=0;i<256;++i)pcm[i]=sample(i);
    unsigned written;
    unsigned ok=hal_audio_write(pcm,256,&written)==HAL_OK && written==256 && hal_audio_start()==HAL_OK;
    wait_ms(20);hal_audio_info_t a;hal_audio_get_info(&a);
    ok=ok && a.played==256 && a.underruns && !a.overruns && !a.errors && !a.amplifier;
    audio_status();return audio_stop() && ok;
}
static unsigned audio_check(void) {
    if(!audio_pio() || !audio_begin())return 0;
    wait_ms(1000);hal_audio_info_t before,after;hal_audio_get_info(&before);
    unsigned ok=audio_ok() && before.played;
    hal_audio_pause();wait_ms(30);hal_audio_get_info(&before);
    wait_ms(30);hal_audio_get_info(&after);ok=ok && after.played==before.played;
    ok=ok && hal_audio_start()==HAL_OK;wait_ms(1000);hal_audio_get_info(&after);
    ok=ok && audio_ok() && after.played>before.played;
    audio_status();return audio_stop() && ok;
}
static unsigned ddr_check(void) {
    volatile uint32_t *words=work;
    static const uint32_t masks[]={0,0xffffffffu,0xaaaaaaaa,0x55555555};
    for(unsigned p=0;p<6;++p) {
        for(unsigned i=0;i<WORK_BYTES/4;++i) {
            words[i]=p<4?masks[p]:p==4?(uint32_t)(uintptr_t)&words[i]:~(uint32_t)(uintptr_t)&words[i];
            if(!(i&255))cooperate();
        }
        __asm__ volatile("fence rw,rw":::"memory");
        for(unsigned i=0;i<WORK_BYTES/4;++i) {
            unsigned expected=p<4?masks[p]:p==4?(uint32_t)(uintptr_t)&words[i]:~(uint32_t)(uintptr_t)&words[i];
            if(words[i]!=expected) {value("DDR mismatch address=",(uint32_t)(uintptr_t)&words[i]);hal_uart_puts("\r\n");return 0;}
            if(!(i&255))cooperate();
        }
    }
    value("DDR scratch bytes=",WORK_BYTES);hal_uart_puts(" (not a full 128MiB test)\r\n");return 1;
}
static unsigned flash_check(void) {
    uint32_t id;unsigned bytes,length;uint8_t uid[16];
    if(hal_flash_probe(&id,&bytes)!=HAL_OK || hal_flash_uid(uid,&length)!=HAL_OK || !length)return 0;
    value("FLASH JEDEC=",id);value(" bytes=",bytes);hal_uart_puts(" UID=");
    static const char hex[]="0123456789ABCDEF";
    for(unsigned i=0;i<length;++i) {hal_uart_putc(hex[uid[i]>>4]);hal_uart_putc(hex[uid[i]&15]);}
    hal_uart_puts(" (read-only, no write verification)\r\n");return 1;
}
static unsigned sd_read(void) {
    if(hal_sd_mount()!=HAL_OK)return 0;
    FIL file;if(f_open(&file,"RVTEST00.BIN",FA_READ)!=FR_OK)return 0;
    uint8_t *buffer=(uint8_t *)work+1;UINT size;unsigned total=0;uint32_t crc=~0u;unsigned ok=1;
    for(;;) {
        if(f_read(&file,buffer,WORK_BYTES,&size)!=FR_OK) {ok=0;break;}
        if(!size)break;
        crc=crc_update(crc,buffer,size);total+=size;cooperate();
    }
    if(f_close(&file)!=FR_OK)ok=0;
    value("SD bytes=",total);value(" crc=",~crc);hal_uart_puts("\r\n");
    return ok && total==4096 && ~crc==0x08040e1eu;
}
static unsigned sd_blocks(void) {
    if(hal_sd_mount()!=HAL_OK)return 0;
    uint8_t *buffer=(uint8_t *)work+1;uint32_t crc=~0u;
    for(unsigned sector=0;sector<16;++sector) {
        if(hal_sd_read(sector,buffer,1)!=HAL_OK)return 0;
        crc=crc_update(crc,buffer,512);cooperate();
    }
    return hal_sd_read(0,buffer,16)==HAL_OK && crc_update(~0u,buffer,8192)==crc;
}
static uint8_t sd_pattern(unsigned offset) {return (offset*17u)^(offset>>8)^0x5au;}
static unsigned sd_write(void) {
    if(hal_sd_mount()!=HAL_OK)return 0;
    FIL file;char name[]="RVT00000.BIN";FRESULT rc=FR_EXIST;
    for(unsigned n=0;n<100 && rc==FR_EXIST;++n) {
        name[6]='0'+n/10;name[7]='0'+n%10;rc=f_open(&file,name,FA_WRITE|FA_CREATE_NEW);
    }
    if(rc!=FR_OK)return 0;
    hal_uart_puts("SD new file=");hal_uart_puts(name);hal_uart_puts("\r\n");
    uint8_t *buffer=(uint8_t *)work+1;unsigned ok=1;UINT size;
    for(unsigned offset=0;offset<65536;offset+=WORK_BYTES) {
        for(unsigned i=0;i<WORK_BYTES;++i)buffer[i]=sd_pattern(offset+i);
        if(f_write(&file,buffer,WORK_BYTES,&size)!=FR_OK || size!=WORK_BYTES) {ok=0;break;}
        cooperate();
    }
    if(f_sync(&file)!=FR_OK)ok=0;
    if(f_close(&file)!=FR_OK || !ok || f_open(&file,name,FA_READ)!=FR_OK)return 0;
    unsigned offset=0;
    for(;;) {
        if(f_read(&file,buffer,WORK_BYTES,&size)!=FR_OK) {ok=0;break;}
        if(!size)break;
        for(unsigned i=0;i<size;++i)if(buffer[i]!=sd_pattern(offset+i))ok=0;
        offset+=size;cooperate();
    }
    return f_close(&file)==FR_OK && ok && offset==65536;
}
static unsigned video_check(void) {
    frame=rgb_lcd_active_read()^1u;volatile uint16_t *pixels=hal_video_frame(frame);
    uint32_t expected=~0u;
    for(unsigned y=0;y<272;++y) {
        for(unsigned x=0;x<480;++x) {
            uint16_t color=x<160?0xf800:x<320?0x07e0:0x001f;
            if(y>=224)color=((x/16)&31)*0x0841u;
            if(!x || x==479 || !y || y==271)color=0xffff;
            if((x<16 && y<16) || (x>=464 && y>=256))color=frame?0xffe0:0x07ff;
            pixels[y*480+x]=color;
            expected=crc_pixel(expected,color);
            if(!(x&127))cooperate();
        }
        if(!(y&3))cooperate();
    }
    __asm__ volatile("fence rw,rw":::"memory");
    uint32_t actual=~0u;
    for(unsigned i=0;i<PIXELS;++i) {
        actual=crc_pixel(actual,pixels[i]);if(!(i&127))cooperate();
    }
    unsigned before=rgb_lcd_completed_read();
    unsigned ok=actual==expected && hal_video_present(frame)==HAL_OK;
    wait_ms(40);hal_video_status();
    return ok && rgb_lcd_completed_read()!=before && !rgb_lcd_underflows_read();
}
static void usb_status(void) {
    hal_usb_info_t u;hal_usb_get_info(&u);
    value("USB initialized=",u.initialized);value(" phy_ready=",u.phy_ready);value(" phy=",u.phy_id);
    value(" connected=",u.connected);value(" VID=",u.vid);value(" PID=",u.pid);value(" HID=",u.hid_interfaces);
    value(" frame=",u.frame);value(" HCCA=",u.hcca);value(" errors=",u.errors);hal_uart_puts("\r\n");
    value("USB key_events=",u.key_events);value(" mouse_events=",u.mouse_events);
    value(" key_drops=",u.key_drops);value(" mouse_drops=",u.mouse_drops);
    value(" report_drops=",u.report_drops);hal_uart_puts("\r\n");
}
static unsigned usb_schedule_ok(unsigned hcca,unsigned interfaces) {
    /* Read a bounded snapshot of the real OHCI periodic ED chain. Disable
       CPU IRQs so the HCD cannot free/reassign TDs during this check. */
    unsigned state=hal_irq_save(),seen[32],count=0,endpoints=0,ok=1;
    unsigned address=*(volatile unsigned *)hcca;
    for(unsigned budget=0;address && budget<16;++budget) {
        if(address<0x40800000u || address>0x40bffff0u || (address&15u)) {ok=0;break;}
        volatile unsigned *ed=(volatile unsigned *)address;
        if(!(ed[0]&(1u<<14))) {
            unsigned head=ed[2]&~15u,tail=ed[1]&~15u;
            if(head<0x40800000u || head>0x40bffff0u || tail<0x40800000u || tail>0x40bffff0u) {ok=0;break;}
            for(unsigned i=0;i<count;++i)if(seen[i]==head || seen[i]==tail)ok=0;
            if(!ok)break;
            seen[count++]=head;seen[count++]=tail;++endpoints;
        }
        address=ed[3];
    }
    hal_irq_restore(state);
    if(address || endpoints<interfaces)ok=0;
    if(!ok)hal_uart_puts("USB periodic TD ownership FAIL\r\n");
    return ok;
}
static unsigned usb_check(void) {
    uint32_t start=hal_time_ms();hal_usb_info_t before,after;
    hal_usb_get_info(&before);if(!before.initialized) {usb_status();return 0;}
    do {cooperate();hal_usb_get_info(&before);}while(!before.connected && hal_time_ms()-start<10000);
    wait_ms(20);hal_usb_get_info(&after);usb_status();
    return after.initialized && after.phy_ready && after.connected && after.hid_interfaces &&
        after.phy_id==0x60424 && !after.phy_error && !after.errors && !after.key_drops && !after.mouse_drops && !after.report_drops &&
        after.frame!=before.frame && after.hcca>=0x40800000 && after.hcca<0x40c00000 && !(after.hcca&255) &&
        usb_schedule_ok(after.hcca,after.hid_interfaces);
}
static unsigned usb_restart(void) {
    hal_usb_stop();wait_ms(20);hal_usb_info_t stopped;hal_usb_get_info(&stopped);
    return !stopped.initialized && !stopped.phy_ready && hal_usb_init()==HAL_OK && usb_check();
}
static void eth_status(void) {
    hal_eth_info_t e;hal_eth_get_info(&e);
    value("ETH ready=",e.initialized);value(" phy=",e.phy_id);value(" link=",e.link);
    value(" rx=",e.rx_frames);value(" tx=",e.tx_frames);value(" drops=",e.rx_drops);
    value(" replies=",network_replies);value(" ignored=",network_ignored);hal_uart_puts("\r\n");
}
static unsigned eth_check(void) {
    hal_eth_info_t e;hal_eth_get_info(&e);uint8_t uid[16];unsigned length;
    if(!e.initialized || hal_flash_uid(uid,&length)!=HAL_OK || !length)return 0;
    uint64_t hash=UINT64_C(14695981039346656037);
    for(unsigned i=0;i<length;++i) {hash^=uid[i];hash*=UINT64_C(1099511628211);}
    uint8_t expected[6];for(unsigned i=0;i<6;++i)expected[i]=hash>>(8*i);
    expected[0]=(expected[0]&0xfeu)|2u;eth_status();
    return !memcmp(e.mac,expected,6) && !e.mdio_errors && !e.crc_errors && !e.preamble_errors &&
        (e.phy_id&0xfffffff0u)==0x001cc810u && e.ref_clock_hz>49000000 && e.ref_clock_hz<51000000;
}
static unsigned eth_start(void) {
    if(!eth_check() || hal_eth_get_mac(echo_mac)!=HAL_OK)return 0;
    network=1;pending_reply=network_replies=network_ignored=0;
    hal_spi_lcd_network(echo_mac,echo_ip);
    hal_uart_puts("ETH echo active IP=169.254.20.20 UDP=1234; host ARP/ping/UDP required\r\n");return 1;
}
void hal_exception_handler(hal_trap_frame_t *f) {
    if(ecall_armed && f->cause==11) {++ecalls;f->pc+=4;return;}
    value("FAULT cause=",f->cause);value(" pc=",f->pc);value(" value=",f->value);hal_uart_puts("\r\n");
    for(;;)__asm__ volatile("nop");
}
static unsigned irq_check(void) {
    hal_stats_t before,after;hal_get_stats(&before);unsigned count=ecalls;
    ecall_armed=1;__asm__ volatile("ecall":::"memory");ecall_armed=0;
    uint32_t start=hal_time_ms();volatile unsigned calculation=0;
    for(unsigned i=0;i<100000;++i)calculation=calculation*33u+i;
    wait_ms(30);hal_get_stats(&after);
    return ecalls==count+1 && calculation==0x73cfeab0u && hal_time_ms()-start>=30 &&
        after.timer_irqs-before.timer_irqs>=20 && !after.unhandled_irqs &&
        hal_deadline_reached(5,0xfffffff0u) && !hal_deadline_reached(0xfffffff0u,5);
}
static unsigned io_check(void) {
    unsigned previous=hal_leds_get(),ok=1;
    for(unsigned i=0;i<6;++i) {hal_leds_set(1u<<i);ok=ok && hal_leds_get()==(1u<<i);wait_ms(100);}
    hal_leds_set(previous);
    ok=ok && hal_ws2812_set(16,8,4)==HAL_OK;
    uint32_t start=hal_time_ms();
    while(hal_ws2812_busy() && hal_time_ms()-start<10)cooperate();
    ok=ok && !hal_ws2812_busy();hal_ws2812_set(0,0,0);
    value("IO keys=",hal_buttons_read());value(" dip=",hal_switches_read());hal_uart_puts(" (visual/input check required)\r\n");
    return ok;
}
static unsigned soak(unsigned seconds) {
    if(!audio_begin())return 0;
    uint32_t start=hal_time_ms();unsigned round=0,ok=1;
    hal_audio_info_t first,last;hal_audio_get_info(&first);
    unsigned lcd_first=rgb_lcd_completed_read();
    hal_uart_puts("SOAK START muted; ! reboots, other input ignored\r\n");
    while(hal_time_ms()-start<seconds*1000u) {
        /* A full CRC round can exceed the nominal one-second wait slot. */
        int ch;while((ch=hal_uart_getc())>=0)if(ch=='!')hal_reboot();
        if(!sd_read() || !video_check() || !usb_check() || !audio_ok()) {ok=0;break;}
        ++round;
        while(hal_time_ms()-start<round*1000u && hal_time_ms()-start<seconds*1000u) {
            cooperate();int ch=hal_uart_getc();if(ch=='!')hal_reboot();
        }
        if(!(round%30)) {value("SOAK rounds=",round);value(" elapsed_ms=",hal_time_ms()-start);hal_uart_puts("\r\n");}
    }
    hal_audio_get_info(&last);ok=ok && last.played>first.played && rgb_lcd_completed_read()>lcd_first;
    value("SOAK rounds=",round);value(" elapsed_ms=",hal_time_ms()-start);audio_status();
    return audio_stop() && ok;
}
void tests_help(void) {
    hal_uart_puts("test ddr|flash|uart|irq|io|sd|sd blocks|sd write|lcd|lcd clear|spi-lcd\r\n");
    hal_uart_puts("test audio|audio pio|audio start|audio stop|audio pause|audio resume|audio tone\r\n");
    hal_uart_puts("test eth|eth parser|eth start|eth stop|usb|usb stop|usb restart|phys|soak 1..300\r\n");
    hal_uart_puts("test usb input|usb input stop|usb leds DEVICE INTERFACE MASK (decimal)\r\n");
}
static unsigned number(const char **text,unsigned *n) {
    const char *p=*text;unsigned value=0;if(*p<'0' || *p>'9')return 0;
    do {value=value*10u+(unsigned)(*p++-'0');if(value>300)return 0;}while(*p>='0' && *p<='9');
    *text=p;*n=value;return 1;
}
int tests_command(const char *command) {
    if(strcmp(command,"test") && strncmp(command,"test ",5))return 0;
    if(!strcmp(command,"test")) {tests_help();return 1;}
    const char *name=command+5;unsigned ok=0,known=1;
    if(!strcmp(name,"ddr"))ok=ddr_check();
    else if(!strcmp(name,"flash"))ok=flash_check();
    else if(!strcmp(name,"uart")) {hal_stats_t s;hal_get_stats(&s);value("UART irqs=",s.uart_irqs);value(" drops=",s.uart_drops);hal_uart_puts(" (received test uart command)\r\n");ok=s.uart_irqs && !s.uart_drops;}
    else if(!strcmp(name,"irq"))ok=irq_check();
    else if(!strcmp(name,"io"))ok=io_check();
    else if(!strcmp(name,"sd"))ok=sd_read();
    else if(!strcmp(name,"sd blocks"))ok=sd_blocks();
    else if(!strcmp(name,"sd write"))ok=sd_write();
    else if(!strcmp(name,"lcd")) {ok=video_check();hal_uart_puts("LCD visual color/orientation check required\r\n");}
    else if(!strcmp(name,"lcd clear"))ok=hal_video_init()==HAL_OK;
    else if(!strcmp(name,"spi-lcd")) {hal_sd_info_t s;hal_sd_get_info(&s);ok=hal_spi_lcd_show(s.initialized)==HAL_OK;hal_uart_puts("SPI LCD visual check required\r\n");}
    else if(!strcmp(name,"audio"))ok=audio_check();
    else if(!strcmp(name,"audio pio"))ok=audio_pio();
    else if(!strcmp(name,"audio start"))ok=audio_begin();
    else if(!strcmp(name,"audio stop"))ok=audio_stop();
    else if(!strcmp(name,"audio pause")) {hal_audio_pause();ok=1;}
    else if(!strcmp(name,"audio resume"))ok=streaming && hal_audio_start()==HAL_OK;
    else if(!strcmp(name,"audio tone")) {ok=streaming || audio_begin();if(ok) {hal_audio_mute(0);audible_until=hal_time_ms()+2000;hal_uart_puts("AUDIO low-amplitude stereo tone for 2s; external capture/listening required\r\n");}}
    else if(!strcmp(name,"eth"))ok=eth_check();
    else if(!strcmp(name,"eth parser"))ok=hal_eth_get_mac(echo_mac)==HAL_OK && packet_check((uint8_t *)work);
    else if(!strcmp(name,"eth start"))ok=eth_start();
    else if(!strcmp(name,"eth stop")) {network=pending_reply=0;hal_eth_info_t e;hal_eth_get_info(&e);hal_spi_lcd_network(e.mac,0);ok=1;}
    else if(!strcmp(name,"usb"))ok=usb_check();
    else if(!strcmp(name,"usb input")) {usb_input=1;hal_uart_puts("TEST usb input READY: raw/key/mouse events; physical input acceptance pending\r\n");return 1;}
    else if(!strcmp(name,"usb input stop")) {usb_input=0;ok=1;}
    else if(!strcmp(name,"usb stop")) {hal_usb_stop();wait_ms(20);hal_usb_info_t u;hal_usb_get_info(&u);usb_status();ok=!u.initialized && !u.phy_ready;}
    else if(!strcmp(name,"usb restart"))ok=usb_restart();
    else if(!strcmp(name,"phys")) {pending_reply=0;ok=hal_phys_reset(10)==HAL_OK && usb_check() && eth_check();}
    else if(!strncmp(name,"soak ",5)) {unsigned seconds;const char *p=name+5;if(number(&p,&seconds) && !*p && seconds)ok=soak(seconds);else known=0;}
    else if(!strncmp(name,"usb leds ",9)) {
        const char *p=name+9;unsigned device,itf,leds;
        if(number(&p,&device) && *p++==' ' && number(&p,&itf) && *p++==' ' && number(&p,&leds) && !*p && device<=255 && itf<4 && leds<32) {
            ok=hal_usb_keyboard_leds(device,itf,leds)==HAL_OK;
            if(ok) {hal_uart_puts("TEST usb leds QUEUED (physical LED completion not verified)\r\n");return 1;}
        } else known=0;
    }
    else known=0;
    if(known)result(name,ok);else {hal_uart_puts("ERR test command/argument\r\n");tests_help();}
    return 1;
}
