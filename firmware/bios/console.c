#include "internal.h"
#include "../common/rgb_canvas.h"
#include "../diagnostics/tests.h"
#include <string.h>
enum {COLS=80,ROWS=34};
static char screen[ROWS][COLS],drawn[2][ROWS][COLS];
static unsigned x,y,slot,video,mode,caps,dirty=1,next_render;
static uint8_t queue[128];static unsigned head,tail;
static struct bios_mouse pointer;
static unsigned pointer_pending;
static void terminal_putc(char c);
static int32_t accumulate(int32_t value,int delta) {value+=delta;return value>32767?32767:value<-32767?-32767:value;}
static void enqueue(unsigned c) {if(c && head-tail<sizeof(queue))queue[head++&127]=c;}
static char key_char(unsigned k,unsigned mods) {
    unsigned shift=!!(mods&0x22);
    if(mods&0xdd)return 0;
    if(k>=4 && k<=29)return (shift^caps?'A':'a')+k-4;
    if(k>=30 && k<=39)return (shift?"!@#$%^&*()":"1234567890")[k-30];
    if(k==40)return '\r';
    if(k==42)return '\b';
    if(k==43)return '\t';
    if(k==44)return ' ';
    static const char low[]="-=\\;\'`,./",high[]="_+|:\"~<>?";
    static const uint8_t codes[]={45,46,49,51,52,53,54,55,56};
    for(unsigned i=0;i<sizeof(codes);++i)if(k==codes[i])return (shift?high:low)[i];
    if(k==47)return shift?'{':'[';
    if(k==48)return shift?'}':']';
    return 0;
}
void bios_console_init(unsigned lcd_ok) {
    (void)fill;(void)text;
    video=lcd_ok;memset(screen,' ',sizeof(screen));memset(drawn,0,sizeof(drawn));x=y=slot=mode=0;dirty=1;
    hal_console_mirror(terminal_putc);
}
static void terminal_putc(char c) {
    if(c=='\r')x=0;
    else if(c=='\n') {x=0;++y;}
    else if(c=='\b') {if(x)--x;}
    else if(c=='\t') {do {screen[y][x++]=' ';}while(x<COLS && x%8);}
    else if((unsigned char)c>=32 && (unsigned char)c<127)screen[y][x++]=c;
    if(x==COLS) {x=0;++y;}
    if(y==ROWS) {for(unsigned r=1;r<ROWS;++r)memcpy(screen[r-1],screen[r],COLS);memset(screen[ROWS-1],' ',COLS);y=ROWS-1;}
    dirty=1;
}
void bios_putc(char c) {hal_uart_putc(c);}
void bios_puts(const char *s) {while(*s)bios_putc(*s++);}
void bios_hex(uint32_t n) {const char h[]="0123456789abcdef";for(int b=28;b>=0;b-=4)bios_putc(h[(n>>b)&15]);}
void bios_decimal(uint32_t n) {char digits[10];unsigned count=0;do {digits[count++]='0'+n%10;n/=10;}while(n);while(count)bios_putc(digits[--count]);}
int bios_getc(void) {return head==tail?-1:queue[tail++&127];}
int bios_mouse_take(struct bios_mouse *event) {
    if(!pointer_pending)return -1;
    *event=pointer;pointer.x=pointer.y=pointer.wheel=0;pointer_pending=0;return 0;
}
int bios_video_mode(unsigned m) {
    if(m>1 || !video)return -1;
    mode=m;if(!mode) {memset(drawn,0,sizeof(drawn));dirty=1;}return 0;
}
void bios_console_poll(void) {
    int c;while((c=hal_uart_getc())>=0)enqueue(c);
    hal_usb_key_t key;while(hal_usb_key_take(&key)==HAL_OK) {
#if MINI_FEATURE_USB
        tests_usb_key(&key);
#endif
        if(key.pressed) {if(key.usage==57)caps^=1;else enqueue(key_char(key.usage,key.modifiers));}
    }
    hal_usb_mouse_t mouse;while(hal_usb_mouse_take(&mouse)==HAL_OK) {
        pointer.buttons=mouse.buttons;pointer.time_ms=mouse.time_ms;
        pointer.x=accumulate(pointer.x,mouse.x);pointer.y=accumulate(pointer.y,mouse.y);pointer.wheel=accumulate(pointer.wheel,mouse.wheel);pointer_pending=1;
#if MINI_FEATURE_USB
        tests_usb_mouse(&mouse);
#endif
    }
    hal_usb_report_t report;while(hal_usb_report_take(&report)==HAL_OK) {
#if MINI_FEATURE_USB
        tests_usb_report(&report);
#endif
    }
    if(!video || mode || !dirty || !hal_deadline_reached(hal_time_ms(),next_render))return;
    unsigned back=slot^1;volatile uint16_t *fb=hal_video_frame(back);
    for(unsigned r=0;r<ROWS;++r) {
        for(unsigned col=0;col<COLS;++col)if(drawn[back][r][col]!=screen[r][col]) {
            cell(fb,col*6,r*8,screen[r][col],1,0xffff,0);drawn[back][r][col]=screen[r][col];
        }
        hal_poll();
    }
    if(hal_video_present(back)==HAL_OK) {slot=back;dirty=0;}
    next_render=hal_time_ms()+16;
}

static unsigned load_started,load_next;
void bios_load_begin(const char *source) {
    load_started=hal_time_ms();load_next=64u*1024u;
    bios_puts("LOAD ");bios_puts(source);bios_puts(" (dot=64 KiB) ");
}
void bios_load_progress(unsigned bytes) {
    while(bytes>=load_next) {bios_putc('.');load_next+=64u*1024u;}
}
void bios_load_end(unsigned bytes,int ok) {
    bios_puts(ok?" done ":" failed ");bios_decimal(bytes);bios_puts(" bytes, ");
    bios_decimal(hal_time_ms()-load_started);bios_puts(" ms\r\n");
}
void bios_boot_timing(const char *phase,unsigned start) {
    bios_puts("BOOT ");bios_puts(phase);bios_puts(": ");
    bios_decimal(hal_time_ms()-start);bios_puts(" ms\r\n");
}
