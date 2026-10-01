/* Minimal DDR-resident base application; public device APIs live in hal/, reusing drivers/ backends. */
#include <hal/hal.h>
#include <string.h>
#include "tests.h"
static int parse_hex(const char *p,unsigned digits,unsigned *value) {
    unsigned n=0;for(unsigned i=0;i<digits;++i) {unsigned char c=p[i];unsigned d=c>='0'&&c<='9'?c-'0':c>='a'&&c<='f'?c-'a'+10:c>='A'&&c<='F'?c-'A'+10:16;if(d>=16)return 0;n=(n<<4)|d;}
    if(p[digits])return 0;
    *value=n;return 1;
}
static void status(void) {
    hal_uart_puts("CPU/sys=60 MHz DDR=120 MHz LCD=9 MHz UART=115200\r\n");
    uint32_t id=0;unsigned bytes=0;unsigned ok=hal_flash_probe(&id,&bytes)==HAL_OK;
    hal_uart_puts("FLASH JEDEC=");hal_uart_hex(id);hal_uart_puts(" bytes=");hal_uart_hex(bytes);hal_uart_puts(ok?" READY\r\n":" UNAVAILABLE\r\n");
    hal_sd_info_t sd;hal_sd_get_info(&sd);
    hal_uart_puts("SD native=");hal_uart_hex(sd.native);hal_uart_puts(" width=");hal_uart_hex(sd.bus_width);hal_uart_puts(" hz=");hal_uart_hex(sd.clock_hz);hal_uart_puts(" sectors=");hal_uart_hex(sd.sectors);hal_uart_puts(" present=");hal_uart_hex(sd.present);hal_uart_puts(" ready=");hal_uart_hex(sd.initialized);hal_uart_puts(" reads=");hal_uart_hex(sd.read_blocks);hal_uart_puts(" writes=");hal_uart_hex(sd.written_blocks);hal_uart_puts(" errors=");hal_uart_hex(sd.errors);hal_uart_puts("\r\n");
    hal_video_status();
    hal_eth_info_t eth;hal_eth_get_info(&eth);
    hal_uart_puts("ETH phy=");hal_uart_hex(eth.phy_id);hal_uart_puts(" addr=");hal_uart_hex(eth.phy_address);
    hal_uart_puts(" ready=");hal_uart_hex(eth.initialized);hal_uart_puts(" link=");hal_uart_hex(eth.link);
    hal_uart_puts(" mbps=");hal_uart_hex(eth.speed_mbps);hal_uart_puts(" full=");hal_uart_hex(eth.full_duplex);
    hal_uart_puts(" ref_hz=");hal_uart_hex(eth.ref_clock_hz);hal_uart_puts(" MAC=");
    const char hex[]="0123456789ABCDEF";
    for(unsigned i=0;i<6;++i) {if(i)hal_uart_putc(':');hal_uart_putc(hex[eth.mac[i]>>4]);hal_uart_putc(hex[eth.mac[i]&15]);}
    hal_uart_puts("\r\n");
    hal_usb_info_t usb;hal_usb_get_info(&usb);
    hal_uart_puts("USB host=48 MHz phy=");hal_uart_hex(usb.phy_id);hal_uart_puts(" ready=");hal_uart_hex(usb.initialized);
    hal_uart_puts(" connected=");hal_uart_hex(usb.connected);hal_uart_puts(" VID=");hal_uart_hex(usb.vid);hal_uart_puts(" PID=");hal_uart_hex(usb.pid);
    hal_uart_puts(" HID=");hal_uart_hex(usb.hid_interfaces);hal_uart_puts(" reports=");hal_uart_hex(usb.reports);hal_uart_puts(" errors=");hal_uart_hex(usb.errors);hal_uart_puts("\r\n");
    hal_audio_info_t audio;hal_audio_get_info(&audio);
    hal_uart_puts("AUDIO hz=");hal_uart_hex(audio.sample_rate);hal_uart_puts(" control=");hal_uart_hex(audio.control);hal_uart_puts(" level=");hal_uart_hex(audio.level);hal_uart_puts(" underruns=");hal_uart_hex(audio.underruns);hal_uart_puts(" errors=");hal_uart_hex(audio.errors);hal_uart_puts(" amp=");hal_uart_hex(audio.amplifier);hal_uart_puts("\r\n");
    hal_stats_t stats;hal_get_stats(&stats);
    hal_uart_puts("IRQ timer=");hal_uart_hex(stats.timer_irqs);hal_uart_puts(" uart=");hal_uart_hex(stats.uart_irqs);hal_uart_puts(" buttons=");hal_uart_hex(stats.button_irqs);hal_uart_puts(" drops=");hal_uart_hex(stats.uart_drops);hal_uart_puts(" unhandled=");hal_uart_hex(stats.unhandled_irqs);hal_uart_puts("\r\n");
    hal_uart_puts("IO leds=");hal_uart_hex(hal_leds_get());hal_uart_puts(" keys=");hal_uart_hex(hal_buttons_read());hal_uart_puts(" dip=");hal_uart_hex(hal_switches_read());hal_uart_puts("\r\n");
}
int main(void) {
    hal_init();unsigned sd_ready=hal_sd_mount()==HAL_OK;unsigned spi_lcd_ready=hal_spi_lcd_show(sd_ready)==HAL_OK;unsigned rgb_ready=hal_video_init()==HAL_OK;
    hal_uart_puts("\r\nriscv-mini | RV32IM | DDR application at 40800000\r\n");
    hal_uart_puts("SYSTEM READY sd=");hal_uart_hex(sd_ready);hal_uart_puts(" spi_lcd=");hal_uart_hex(spi_lcd_ready);
    hal_uart_puts(" rgb_lcd=");hal_uart_hex(rgb_ready);hal_uart_puts("\r\n");
    hal_eth_info_t identity;hal_eth_get_info(&identity);
    if(spi_lcd_ready && identity.uid_length)hal_spi_lcd_network(identity.mac,0);
    unsigned last_link=0;
    status();hal_uart_puts("Commands: help, status, ls, reboot, io, led HH, rgb RRGGBB, test (! also resets)\r\n> ");
    char line[32];unsigned used=0,overflow=0;
    for(;;) {
        hal_poll();tests_poll();
        hal_eth_info_t link;hal_eth_get_info(&link);
        if(spi_lcd_ready && link.link!=last_link) {hal_spi_lcd_link(link.link);last_link=link.link;}
        unsigned pressed,released;hal_buttons_take(&pressed,&released);
        if(pressed|released) {hal_uart_puts("\r\nBUTTON pressed=");hal_uart_hex(pressed);hal_uart_puts(" released=");hal_uart_hex(released);hal_uart_puts("\r\n> ");}
        int received=hal_uart_getc();if(received<0)continue;
        unsigned ch=received;
        if(ch=='!') {hal_reboot();}
        if(ch=='\n') continue;
        if(ch=='\r') {
            line[used]=0;hal_uart_puts("\r\n");
            if(overflow) hal_uart_puts("ERR command too long\r\n");
            else if(!strcmp(line,"reboot")) {hal_reboot();}
            else if(!strcmp(line,"status") || !strcmp(line,"io")) status();
            else if(!strncmp(line,"led ",4)) {unsigned value;if(!parse_hex(line+4,2,&value)||value>63)hal_uart_puts("ERR led mask\r\n");else hal_leds_set(value);}
            else if(!strncmp(line,"rgb ",4)) {unsigned value;if(!parse_hex(line+4,6,&value))hal_uart_puts("ERR rgb color\r\n");else if(hal_ws2812_set(value>>16,value>>8,value)!=HAL_OK)hal_uart_puts("BUSY rgb\r\n");}
            else if(!strcmp(line,"ls")) hal_sd_list();
            else if(!strcmp(line,"help")) {hal_uart_puts("help, status, ls, reboot, io, led HH, rgb RRGGBB, test\r\n");tests_help();}
            else if(tests_command(line)) {}
            else if(used) hal_uart_puts("ERR unknown command\r\n");
            used=overflow=0;hal_uart_puts("> ");
        } else if(ch==8 || ch==127) {if(used) {--used;hal_uart_puts("\b \b");}}
        else if(ch>=32 && ch<127) {hal_uart_putc(ch);if(used<sizeof(line)-1) line[used++]=ch;else overflow=1;}
    }
}
