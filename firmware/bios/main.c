#include "internal.h"
#include "../diagnostics/tests.h"
#include <string.h>
static void network_display(void) {
    uint8_t mac[6];if(hal_eth_get_mac(mac)==HAL_OK)hal_spi_lcd_network(mac,bios_settings.ip);
}
void bios_poll(void) {
    static unsigned last_link=2;
    hal_poll();bios_console_poll();
    hal_eth_info_t eth;hal_eth_get_info(&eth);
    if(eth.link!=last_link) {hal_spi_lcd_link(eth.link);last_link=eth.link;}
}
static void report(const char *name,int ok) {bios_puts("POST ");bios_puts(name);bios_puts(ok?" PASS\r\n":" unavailable/FAIL\r\n");}
void bios_post(void) {
    static volatile uint32_t scratch[256];unsigned ok=1;
    for(unsigned i=0;i<256;++i)scratch[i]=0x5a5a0000u+i;
#if MINI_CPU_DCACHE
    __asm__ volatile("fence rw,rw\n.word 0x0000500f":::"memory");
#endif
    for(unsigned i=0;i<256;++i)if(scratch[i]!=0x5a5a0000u+i)ok=0;
    report("DDR scratch",ok);
    uint32_t start=hal_time_ms();hal_delay_ms(5);report("TIMER",hal_time_ms()-start>=5);
    uint32_t id;unsigned bytes;report("FLASH",hal_flash_probe(&id,&bytes)==HAL_OK);
    report("SD",hal_sd_mount()==HAL_OK);
    hal_eth_info_t eth;hal_eth_get_info(&eth);report("ETH PHY",eth.initialized);
    hal_usb_info_t usb;hal_usb_get_info(&usb);report("USB PHY",usb.initialized);
    uint32_t ms=hal_time_ms();while((uint32_t)(hal_time_ms()-ms)<1500)bios_poll();
    hal_usb_get_info(&usb);report("USB HID",usb.connected && usb.hid_interfaces);
    int t=bios_call(BIOS_TIME,0,0,0,0);report("BIOS ecall",(uint32_t)t>=ms);
}
static void settings_show(void) {
    bios_puts("boot=");bios_puts(bios_settings.boot==1?"sd":bios_settings.boot==2?"net":"none");
    bios_puts(" delay_ms=");bios_decimal(bios_settings.delay_ms);bios_puts(" file=");bios_puts(bios_settings.file);bios_puts("\r\n");
    bios_puts("IP=");for(unsigned i=0;i<4;++i) {if(i)bios_putc('.');bios_decimal(bios_settings.ip[i]);}
    bios_puts(" server=");for(unsigned i=0;i<4;++i) {if(i)bios_putc('.');bios_decimal(bios_settings.server[i]);}bios_puts("\r\n");
}
static int parse_ip(const char *p,uint8_t ip[4]) {
    uint8_t v[4];for(unsigned i=0;i<4;++i) {unsigned n=0,d=0;
        while(*p>='0' && *p<='9') {n=n*10+*p++-'0';if(++d>3 || n>255)return 0;}
        if(!d)return 0;
        v[i]=n;if(i<3 && *p++!='.')return 0;
    }if(*p)return 0;memcpy(ip,v,4);return 1;
}
static int parse_hex(const char *p,unsigned digits,unsigned *value) {
    unsigned n=0;
    for(unsigned i=0;i<digits;++i) {
        unsigned c=(unsigned char)p[i],v;
        if(c>='0' && c<='9')v=c-'0';else if(c>='a' && c<='f')v=c-'a'+10;else if(c>='A' && c<='F')v=c-'A'+10;else return 0;
        n=(n<<4)|v;
    }
    if(p[digits])return 0;
    *value=n;return 1;
}
static void command(char *s) {
    if(!strcmp(s,"help")) {bios_puts("help, status, post, ls, tty, graphics, boot sd [file], boot net [file]\r\nfetch FILE (TFTP to new SD file), settings, settings save/load/defaults\r\nset boot none/sd/net, set file NAME, set delay 0..30000\r\nset ip A.B.C.D, set server A.B.C.D, io, led HH, rgb RRGGBB\r\ntest bios, test ... (test alone lists diagnostics), reboot\r\n");return;}
    if(!strcmp(s,"status")) {
        bios_puts("CPU/sys=60 MHz DDR=120 MHz UART=115200\r\nBIOS ABI=1 memory=128 MiB MMU=");bios_decimal(MINI_FEATURE_MMU);
        bios_puts(" FPU=");bios_decimal(MINI_FEATURE_FPU);bios_puts("\r\n");settings_show();hal_video_status();
        uint32_t id;unsigned bytes;
        if(hal_flash_probe(&id,&bytes)==HAL_OK) {bios_puts("FLASH JEDEC=");bios_hex(id);bios_puts(" bytes=");bios_hex(bytes);bios_puts("\r\n");}
        hal_audio_info_t audio;hal_audio_get_info(&audio);
        bios_puts("AUDIO hz=");bios_hex(audio.sample_rate);bios_puts(" control=");bios_hex(audio.control);bios_puts(" level=");bios_hex(audio.level);
        bios_puts(" underruns=");bios_hex(audio.underruns);bios_puts(" errors=");bios_hex(audio.errors);bios_puts(" amp=");bios_hex(audio.amplifier);bios_puts("\r\n");
        hal_stats_t stats;hal_get_stats(&stats);bios_puts("IRQ timer=");bios_hex(stats.timer_irqs);bios_puts(" drops=");bios_hex(stats.uart_drops);
        bios_puts(" unhandled=");bios_hex(stats.unhandled_irqs);bios_puts("\r\n");return;
    }
    if(!strcmp(s,"io")) {bios_puts("buttons=");bios_hex(hal_buttons_read());bios_puts(" switches=");bios_hex(hal_switches_read());bios_puts(" leds=");bios_hex(hal_leds_get());bios_puts("\r\n");return;}
    if(!strncmp(s,"led ",4)) {unsigned v;if(!parse_hex(s+4,2,&v) || v>63)bios_puts("ERR led mask\r\n");else hal_leds_set(v);return;}
    if(!strncmp(s,"rgb ",4)) {unsigned v;if(!parse_hex(s+4,6,&v))bios_puts("ERR rgb color\r\n");else if(hal_ws2812_set(v>>16,v>>8,v)!=HAL_OK)bios_puts("ERR rgb unavailable/busy\r\n");return;}
    if(!strcmp(s,"post")) {bios_post();return;}
    if(!strcmp(s,"test bios")) {bios_puts(bios_self_test()?"BIOS TEST PASS\r\n":"BIOS TEST FAIL\r\n");return;}
    if(!strcmp(s,"ls")) {hal_sd_list();return;}
    if(!strcmp(s,"reboot"))hal_reboot();
    if(!strcmp(s,"tty")) {bios_video_mode(0);return;}
    if(!strcmp(s,"graphics")) {bios_video_mode(1);return;}
    if(!strcmp(s,"settings")) {settings_show();return;}
    if(!strcmp(s,"settings defaults")) {bios_settings_default();network_display();settings_show();return;}
    if(!strcmp(s,"settings save")) {bios_puts(bios_settings_save()?"SETTINGS SAVED\r\n":"SETTINGS SAVE FAIL\r\n");return;}
    if(!strcmp(s,"settings load")) {bios_puts(bios_settings_load()?"SETTINGS LOADED\r\n":"SETTINGS LOAD FAIL\r\n");network_display();return;}
    if(!strncmp(s,"set boot ",9)) {if(!strcmp(s+9,"none"))bios_settings.boot=0;else if(!strcmp(s+9,"sd"))bios_settings.boot=1;else if(!strcmp(s+9,"net"))bios_settings.boot=2;else bios_puts("ERR boot value\r\n");return;}
    if(!strncmp(s,"set file ",9)) {unsigned n=strlen(s+9);if(n && n<sizeof(bios_settings.file))memcpy(bios_settings.file,s+9,n+1);else bios_puts("ERR filename\r\n");return;}
    if(!strncmp(s,"set ip ",7)) {if(!parse_ip(s+7,bios_settings.ip))bios_puts("ERR IP\r\n");else network_display();return;}
    if(!strncmp(s,"set server ",11)) {if(!parse_ip(s+11,bios_settings.server))bios_puts("ERR IP\r\n");return;}
    if(!strncmp(s,"set delay ",10)) {
        const char *p=s+10;unsigned n=0,d=0;
        while(*p>='0' && *p<='9' && d<6) {n=n*10+*p++-'0';++d;}
        if(!d || *p || n>30000)bios_puts("ERR delay\r\n");else bios_settings.delay_ms=n;
        return;
    }
    if(!strncmp(s,"fetch ",6) && s[6]) {bios_fetch(s+6);return;}
    if(!strcmp(s,"boot sd") || !strncmp(s,"boot sd ",8)) {bios_sd_boot(s[7]?s+8:bios_settings.file);return;}
    if(!strcmp(s,"boot net") || !strncmp(s,"boot net ",9)) {bios_net_boot(bios_settings.server,s[8]?s+9:bios_settings.file);return;}
    if(!strncmp(s,"test lcd",8) || !strncmp(s,"test soak",9))bios_video_mode(BIOS_GRAPHICS);
    if(tests_command(s))return;
    if(*s)bios_puts("ERR unknown command\r\n");
}
int main(void) {
    hal_init();unsigned video_ok=hal_video_init()==HAL_OK;
    bios_console_init(video_ok);bios_puts("\r\nRISCV MINI BIOS 1\r\n");
    report("RGB LCD",video_ok);
    bios_settings_default();bios_settings_load();
    unsigned sd_ok=hal_sd_mount()==HAL_OK;hal_spi_lcd_show(sd_ok);
    network_display();
    bios_post();bios_puts("SYSTEM READY - BIOS\r\n");
    if(bios_settings.boot) {
        bios_puts("Press any key to enter setup\r\n");unsigned end=hal_time_ms()+bios_settings.delay_ms,skip=0;
        while(!hal_deadline_reached(hal_time_ms(),end)) {bios_poll();if(bios_getc()>=0) {skip=1;break;}}
        if(!skip) {if(bios_settings.boot==1)bios_sd_boot(bios_settings.file);else bios_net_boot(bios_settings.server,bios_settings.file);}
    }
    bios_puts("> ");char line[128];unsigned n=0,overflow=0;
    for(;;) {
        bios_poll();tests_poll();int c=bios_getc();if(c<0)continue;
        if(c=='!')hal_reboot();
        if(c=='\n')continue;
        if(c=='\r') {line[n]=0;bios_puts("\r\n");if(overflow)bios_puts("ERR command too long\r\n");else command(line);n=overflow=0;bios_puts("> ");}
        else if(c==8 || c==127) {if(n) {--n;bios_puts("\b \b");}}
        else if(c>=32 && c<127) {if(n<sizeof(line)-1) {line[n++]=c;bios_putc(c);}else overflow=1;}
    }
}
