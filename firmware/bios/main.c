#include "internal.h"
#include "../diagnostics/tests.h"
#include "image_abi.h"
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
int parse_ip(const char *p,uint8_t ip[4]) {
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
/* md 用：解析至多 8 位十六进制（可带 0x），成功则推进 *pp。 */
static int parse_u32(const char **pp,uint32_t *value) {
    const char *p=*pp;unsigned n=0,digits=0;
    if(p[0]=='0' && (p[1]=='x' || p[1]=='X'))p+=2;
    for(;digits<8;++digits) {
        unsigned c=(unsigned char)*p,v;
        if(c>='0' && c<='9')v=c-'0';
        else if(c>='a' && c<='f')v=c-'a'+10;
        else if(c>='A' && c<='F')v=c-'A'+10;
        else break;
        n=(n<<4)|v;++p;
    }
    if(!digits)return 0;
    *pp=p;*value=n;return 1;
}
static void hex8(uint32_t v) {const char *d="0123456789abcdef";bios_putc(d[(v>>4)&15]);bios_putc(d[v&15]);}
/* md/mdb 只允许读 RAM 窗口：CSR（0xf0000000 起）读取有副作用，
 * 未映射地址会触发 load fault 让 BIOS 重启，两者都不适合随手探测。 */
static int readable_range(uint32_t address,uint32_t length) {
    static const struct {uint32_t base,limit;} window[]={{0x00000000u,0x08000000u},{0x40000000u,0x48000000u}};
    if(!length || length>0x01000000u)return 0;
    if(address+length<address)return 0;
    for(unsigned i=0;i<2;++i)
        if(address>=window[i].base && address+length<=window[i].limit)return 1;
    return 0;
}
static void command(char *s) {
    if(bios_benchmark_command(s))return;
    if(!strcmp(s,"help")) {bios_puts("help, status, post, ls, tty, graphics, boot sd [file], boot net [file]\r\nfetch FILE (TFTP to new SD file), settings, settings save/load/defaults\r\nset boot none/sd/net, set file NAME, set delay 0..30000\r\nset ip A.B.C.D, set server A.B.C.D, ping [A.B.C.D] [1..32], io, led HH, rgb RRGGBB\r\nbench [all|cpu|mem|cache|libc|io|net FILE|sd HZ], test bios, test ... (test alone lists diagnostics), reboot\r\nmd/mdb ADDR [COUNT] [u] word/byte dump (ADDR hex, COUNT dec, RAM only, u=UART only)\r\n");return;}
    if(!strcmp(s,"status")) {
        bios_puts("CPU/sys=60 MHz DDR=120 MHz UART=115200\r\nBIOS ABI=1 memory=128 MiB MMU=");bios_decimal(MINI_FEATURE_MMU);
        bios_puts(" FPU=");bios_decimal(MINI_FEATURE_FPU);bios_puts("\r\n");bios_puts("BUILD " MINI_APP_ID " " MINI_BUILD_RTL_ID " " MINI_BUILD_ROM_ID "\r\n");settings_show();hal_video_status();
        uint32_t id;unsigned bytes;
        if(hal_flash_probe(&id,&bytes)==HAL_OK) {bios_puts("FLASH JEDEC=");bios_hex(id);bios_puts(" bytes=");bios_hex(bytes);bios_puts("\r\n");}
        hal_audio_info_t audio;hal_audio_get_info(&audio);
        bios_puts("AUDIO hz=");bios_hex(audio.sample_rate);bios_puts(" control=");bios_hex(audio.control);bios_puts(" level=");bios_hex(audio.level);
        bios_puts(" underruns=");bios_hex(audio.underruns);bios_puts(" errors=");bios_hex(audio.errors);bios_puts(" amp=");bios_hex(audio.amplifier);bios_puts("\r\n");
        hal_stats_t stats;hal_get_stats(&stats);bios_puts("IRQ timer=");bios_hex(stats.timer_irqs);bios_puts(" drops=");bios_hex(stats.uart_drops);
        bios_puts(" unhandled=");bios_hex(stats.unhandled_irqs);bios_puts("\r\n");
        bios_puts("FB0=");bios_hex((uint32_t)(uintptr_t)hal_video_frame(0));
        bios_puts(" FB1=");bios_hex((uint32_t)(uintptr_t)hal_video_frame(1));
        bios_puts(" TTY=");bios_hex((uint32_t)bios_console_grid());
        bios_puts(" (md ADDR COUNT)\r\n");return;
    }
    if(!strcmp(s,"io")) {bios_puts("buttons=");bios_hex(hal_buttons_read());bios_puts(" switches=");bios_hex(hal_switches_read());bios_puts(" leds=");bios_hex(hal_leds_get());bios_puts("\r\n");return;}
    if(!strncmp(s,"mdb ",4) || !strncmp(s,"md ",3)) {
        int bytes=(s[2]=='b');
        const char *p=s+(bytes?4:3);
        uint32_t address,count=bytes?32:16;
        int uart_only=0;
        if(!parse_u32(&p,&address)) {bios_puts("ERR md ADDR [COUNT] [u] (ADDR hex, COUNT decimal, u=UART only)\r\n");return;}
        while(*p==' ')++p;
        if(*p && *p!='u') {
            uint32_t n=0;unsigned d=0;
            while(*p>='0' && *p<='9' && d<7) {n=n*10+*p++-'0';++d;}
            if(!d || !n) {bios_puts("ERR md count\r\n");return;}
            count=n;
            while(*p==' ')++p;
        }
        if(*p=='u' && !p[1])uart_only=1;
        else if(*p) {bios_puts("ERR md trailing argument (only 'u')\r\n");return;}
        if(count>256u)count=256u;   /* 上限 256 项：字 dump=1 KiB、字节 dump=256 B */
        if(!bytes && (address&3u)) {bios_puts("ERR md needs a word-aligned ADDR\r\n");return;}
        uint32_t span=count*(bytes?1u:4u);
        if(!readable_range(address,span)) {bios_puts("ERR md range (RAM windows only)\r\n");return;}
        if(uart_only)bios_console_uart_only(1);
        if(bytes) {
            volatile const uint8_t *q=(volatile const uint8_t *)(uintptr_t)address;
            for(uint32_t i=0;i<count;++i) {
                if(!(i&15u)) {if(i)bios_puts("\r\n");bios_hex(address+i);bios_putc(':');}
                bios_putc(' ');hex8(q[i]);
            }
        } else {
            volatile const uint32_t *q=(volatile const uint32_t *)(uintptr_t)address;
            for(uint32_t i=0;i<count;++i) {
                if(!(i&3u)) {if(i)bios_puts("\r\n");bios_hex(address+i*4u);bios_putc(':');}
                bios_putc(' ');bios_hex(q[i]);
            }
        }
        bios_puts("\r\n");
        if(uart_only)bios_console_uart_only(0);
        return;
    }
    if(!strncmp(s,"led ",4)) {unsigned v;if(!parse_hex(s+4,2,&v) || v>63)bios_puts("ERR led mask\r\n");else hal_leds_set(v);return;}
    if(!strncmp(s,"rgb ",4)) {unsigned v;if(!parse_hex(s+4,6,&v))bios_puts("ERR rgb color\r\n");else if(hal_ws2812_set(v>>16,v>>8,v)!=HAL_OK)bios_puts("ERR rgb unavailable/busy\r\n");return;}
    if(!strcmp(s,"post")) {bios_post();return;}
    if(!strcmp(s,"test bios")) {bios_puts(bios_self_test()?"BIOS TEST PASS\r\n":"BIOS TEST FAIL\r\n");return;}
    if(!strcmp(s,"ls")) {bios_sd_list();return;}
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
    if(!strncmp(s,"ping",4)) {
        const char *p=s+4;unsigned count=4;
        while(*p==' ')++p;
        if(!*p) {
            char server[16];unsigned n=0;
            for(unsigned i=0;i<4;++i) {
                if(i)server[n++]='.';
                unsigned v=bios_settings.server[i],w=1;
                while(v>=10*w) w*=10;
                while(w) {server[n++]='0'+v/w;v%=w;w/=10;}
            }
            server[n]=0;bios_ping(server,count);return;
        }
        const char *e=p;while(*e && *e!=' ')++e;
        char target[17];unsigned n=(unsigned)(e-p);
        if(!n || n>16) {bios_puts("ERR ping target\r\n");return;}
        memcpy(target,p,n);target[n]=0;
        p=e;while(*p==' ')++p;
        if(*p) {unsigned v=0;const char *q=p;while(*q>='0' && *q<='9')++q;
            if(q==p) {bios_puts("ERR ping count\r\n");return;}
            for(const char *t=p;t<q;++t)v=v*10+*t-'0';
            if(!v || v>32) {bios_puts("ERR ping count\r\n");return;}
            count=v;}
        bios_ping(target,count);return;
    }
    if(!strncmp(s,"test lcd",8) || !strncmp(s,"test soak",9))bios_video_mode(BIOS_GRAPHICS);
    if(tests_command(s))return;
    if(*s)bios_puts("ERR unknown command\r\n");
}
int main(void) {
    hal_init();unsigned video_ok=hal_video_init()==HAL_OK;
    bios_console_init(video_ok);bios_puts("\r\nRISCV MINI BIOS " MINI_APP_ID " " MINI_BUILD_ROM_ID "\r\n");
    report("RGB LCD",video_ok);
    bios_settings_default();bios_settings_load();
    unsigned sd_ok=hal_sd_mount()==HAL_OK;hal_spi_lcd_show(sd_ok);
    network_display();
    bios_post();bios_puts("SYSTEM READY - BIOS\r\n");
    bios_puts("STATE=app_ready stage=bios abi=" MINI_IMAGE_ABI_HEX " id=" MINI_APP_ID "\r\n");
    if(bios_settings.boot) {
        bios_puts("Press any key to enter setup\r\n");unsigned end=hal_time_ms()+bios_settings.delay_ms,skip=0;
        while(!hal_deadline_reached(hal_time_ms(),end)) {bios_poll();if(bios_getc()>=0) {skip=1;break;}}
        if(!skip) {if(bios_settings.boot==1)bios_sd_boot(bios_settings.file);else bios_net_boot(bios_settings.server,bios_settings.file);}
    }
    // 行编辑：退格/删除、方向键、Home/End、Insert 切换、Ctrl-A/E/K/U/W。
    // 行尾追加与行尾退格保持原始逐字节回显（不重发提示符），保证板测脚本
    // 的 until(b'> ') 仍只匹配真正的提示符；只有行中间编辑才整行重绘。
    char line[128];unsigned n=0,caret=0,overflow=0,insert=0;
    int esc_state=0;unsigned esc_value=0;
    bios_puts("> ");
    for(;;) {
        bios_poll();tests_poll();int c=bios_getc();if(c<0)continue;
        if(c=='!')hal_reboot();
        if(c=='\n')continue;
        // ESC [ <digits> <final> 与 ESC O <final>：分字节推进状态，避免阻塞等后续字节。
        if(esc_state) {
            if(esc_state==1) {
                if(c=='[') {esc_state=2;esc_value=0;continue;}
                if(c=='O') {esc_state=3;continue;}
                esc_state=0;continue;            // 其它 ESC 序列：忽略
            }
            if(esc_state==2) {
                if(c>='0' && c<='9') {esc_value=esc_value*10+(unsigned)(c-'0');continue;}
                if(c==';')continue;              // 参数分隔：本编辑器只用第一个参数
            }
            esc_state=0;
            if(c=='D') {if(caret)--caret;}
            else if(c=='C') {if(caret<n)++caret;}
            else if(c=='A'||c=='B')continue;      // 没有编辑历史，忽略上下键
            else if(c=='H')caret=0;
            else if(c=='F')caret=n;
            else if(c=='~') {
                if(esc_value==1||esc_value==7)caret=0;
                else if(esc_value==4||esc_value==8)caret=n;
                else if(esc_value==2)insert^=1;
                else if(esc_value==3 && caret<n) {for(unsigned i=caret;i+1<n;++i)line[i]=line[i+1];--n;}
            }
            goto redraw;
        }
        if(c==0x1b) {esc_state=1;continue;}
        if(c=='\r') {
            line[n]=0;bios_puts("\r\n");
            if(overflow)bios_puts("ERR command too long\r\n");else command(line);
            n=caret=overflow=insert=0;
            bios_puts("> ");continue;
        }
        if(c==8 || c==127) {                     // 退格/Delete
            if(!caret)continue;
            if(caret==n) {--n;--caret;bios_puts("\b \b");continue;}  // 行尾：原样回显
            --caret;
            for(unsigned i=caret;i+1<n;++i)line[i]=line[i+1];
            --n;
        }
        else if(c==1)caret=0;                    // Ctrl-A
        else if(c==5)caret=n;                    // Ctrl-E
        else if(c==11) {for(unsigned i=caret;i<n;++i)line[i]=line[i+1];n=caret;}  // Ctrl-K
        else if(c==21) {n=caret=0;}              // Ctrl-U
        else if(c==23) {                         // Ctrl-W：删前一个词
            unsigned end=caret;
            while(caret && line[caret-1]==' ')--caret;
            while(caret && line[caret-1]!=' ')--caret;
            unsigned removed=end-caret;
            for(unsigned i=end;i<n;++i)line[i-removed]=line[i];
            n-=removed;
        }
        else if(c>=32 && c<127) {
            if(n>=sizeof(line)-1) {overflow=1;continue;}
            if(caret<n) {                        // 行中间：按 insert 决定插入还是覆盖
                if(insert) {                     // 插入：尾部右移，必须整行重绘
                    for(unsigned i=n;i>caret;--i)line[i]=line[i-1];
                    line[caret++]=c;++n;
                    goto redraw;
                }
                line[caret++]=c;                 // 覆盖：就地替换，长度不变
                bios_putc((char)c);continue;     // 物理光标正在 caret 处
            }
            line[caret++]=c;++n;bios_putc((char)c);continue;  // 行尾追加：原样回显
        }
        else continue;
redraw:
        bios_puts("\r\x1b[K> ");                 // CR + EL + 提示符 + 整行
        for(unsigned i=0;i<n;++i)bios_putc(line[i]);
        if(caret<n) {                            // 光标归位：CSI <n-caret> D
            unsigned back=n-caret;char digits[8];int k=0;
            do {digits[k++]=(char)('0'+back%10);back/=10;}while(back);
            bios_puts("\x1b[");
            while(k)bios_putc(digits[--k]);
            bios_putc('D');
        }
    }
}
