/* UART-loaded RGB LCD demo. Only the inactive DDR framebuffer is changed. */
#include <hal/hal.h>
#include <generated/csr.h>

enum { WIDTH=480, HEIGHT=272, COLS=36, ROWS=8, CW=12, CH=18, STATUS_COLS=76 };
enum { BACKGROUND=0x1084, PANEL=0x0022, WHITE=0xffff, CYAN=0x07ff, GRAY=0x8c71 };
static char input[ROWS][COLS], painted[2][ROWS][COLS], status_painted[2][3][STATUS_COLS];
static unsigned row, column, caps, active, revision=1, rendered, renders, failures;
static unsigned key_down, key_up, mouse_events, last_usage, last_pressed, modifiers, buttons;
static int mouse_x=WIDTH/2, mouse_y=HEIGHT/2, wheel;
static struct {int x,y;unsigned drawn;uint16_t pixels[12*18];} cursor[2];

/* Original 5x7 row glyphs, bit 4 leftmost; uppercase/digits match the SPI LCD. */
static const uint8_t upper[26][7]={
 {14,17,17,31,17,17,17},{30,17,17,30,17,17,30},{14,17,16,16,16,17,14},
 {30,17,17,17,17,17,30},{31,16,16,30,16,16,31},{31,16,16,30,16,16,16},
 {14,17,16,23,17,17,14},{17,17,17,31,17,17,17},{14,4,4,4,4,4,14},
 {7,2,2,2,2,18,12},{17,18,20,24,20,18,17},{16,16,16,16,16,16,31},
 {17,27,21,21,17,17,17},{17,25,21,19,17,17,17},{14,17,17,17,17,17,14},
 {30,17,17,30,16,16,16},{14,17,17,17,21,18,13},{30,17,17,30,20,18,17},
 {15,16,16,14,1,1,30},{31,4,4,4,4,4,4},{17,17,17,17,17,17,14},
 {17,17,17,17,17,10,4},{17,17,17,21,21,21,10},{17,17,10,4,10,17,17},
 {17,17,10,4,4,4,4},{31,1,2,4,8,16,31}};
static const uint8_t lower[26][7]={
 {0,0,14,1,15,17,15},{16,16,30,17,17,17,30},{0,0,14,17,16,17,14},
 {1,1,15,17,17,17,15},{0,0,14,17,31,16,14},{6,9,8,28,8,8,8},
 {0,14,17,17,15,1,14},{16,16,30,17,17,17,17},{4,0,12,4,4,4,14},
 {2,0,6,2,2,18,12},{16,16,18,20,24,20,18},{12,4,4,4,4,4,14},
 {0,0,26,21,21,21,21},{0,0,30,17,17,17,17},{0,0,14,17,17,17,14},
 {0,30,17,17,30,16,16},{0,15,17,17,15,1,1},{0,0,22,25,16,16,16},
 {0,0,15,16,14,1,30},{8,8,28,8,8,9,6},{0,0,17,17,17,19,13},
 {0,0,17,17,17,10,4},{0,0,17,17,21,21,10},{0,0,17,10,4,10,17},
 {0,17,17,17,15,1,14},{0,0,31,2,4,8,31}};
static const uint8_t digits[10][7]={
 {14,17,19,21,25,17,14},{4,12,4,4,4,4,14},{14,17,1,2,4,8,31},
 {30,1,1,14,1,1,30},{2,6,10,18,31,2,2},{31,16,16,30,1,1,30},
 {14,16,16,30,17,17,14},{31,1,2,4,8,8,8},{14,17,17,14,17,17,14},
 {14,17,17,15,1,1,14}};
static const char punctuation[]="!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~";
static const uint8_t symbols[][7]={
 {4,4,4,4,4,0,4},{10,10,0,0,0,0,0},{10,31,10,10,31,10,0},
 {4,15,20,14,5,30,4},{24,25,2,4,8,19,3},{12,18,20,8,21,18,13},
 {4,4,8,0,0,0,0},{2,4,8,8,8,4,2},{8,4,2,2,2,4,8},
 {0,21,14,31,14,21,0},{0,4,4,31,4,4,0},{0,0,0,0,4,4,8},
 {0,0,0,31,0,0,0},{0,0,0,0,0,4,4},{1,2,2,4,8,8,16},
 {0,4,4,0,4,4,0},{0,4,4,0,4,4,8},{2,4,8,16,8,4,2},
 {0,0,31,0,31,0,0},{8,4,2,1,2,4,8},{14,17,1,2,4,0,4},
 {14,17,23,21,23,16,14},{14,8,8,8,8,8,14},{16,8,8,4,2,2,1},
 {14,2,2,2,2,2,14},{4,10,17,0,0,0,0},{0,0,0,0,0,0,31},
 {8,4,2,0,0,0,0},{2,4,4,8,4,4,2},{4,4,4,4,4,4,4},
 {8,4,4,2,4,4,8},{0,0,9,22,0,0,0}};
static const uint8_t blank[7]={0};
static const uint8_t *glyph(char ch) {
    if(ch>='A' && ch<='Z')return upper[ch-'A'];
    if(ch>='a' && ch<='z')return lower[ch-'a'];
    if(ch>='0' && ch<='9')return digits[ch-'0'];
    for(unsigned i=0;i<sizeof(punctuation)-1;++i)if(ch==punctuation[i])return symbols[i];
    return blank;
}
static void cell(volatile uint16_t *fb,unsigned x,unsigned y,char ch,unsigned scale,uint16_t fg,uint16_t bg) {
    const uint8_t *bits=glyph(ch);
    for(unsigned dy=0;dy<8*scale;++dy)for(unsigned dx=0;dx<6*scale;++dx)
        fb[(y+dy)*WIDTH+x+dx]=(dy<7*scale && dx<5*scale && (bits[dy/scale]&(16u>>(dx/scale))))?fg:bg;
}
static void text(volatile uint16_t *fb,unsigned x,unsigned y,const char *s,unsigned scale,uint16_t fg,uint16_t bg) {
    while(*s && x+6*scale<=WIDTH) {cell(fb,x,y,*s++,scale,fg,bg);x+=6*scale;}
}
static void fill(volatile uint16_t *fb,unsigned x,unsigned y,unsigned w,unsigned h,uint16_t color) {
    for(unsigned dy=0;dy<h;++dy) {
        for(unsigned dx=0;dx<w;++dx)fb[(y+dy)*WIDTH+x+dx]=color;
        if(!(dy&7u))hal_poll();
    }
}
static void background(unsigned slot) {
    volatile uint16_t *fb=hal_video_frame(slot);
    fill(fb,0,0,WIDTH,HEIGHT,BACKGROUND);
    text(fb,12,10,"USB KEYBOARD / MOUSE",2,CYAN,BACKGROUND);
    text(fb,12,34,"Type here. Shift/Caps, Enter, Backspace. Esc clears.",1,WHITE,BACKGROUND);
    text(fb,12,46,"Move mouse. Buttons change cursor color; wheel is shown below.",1,GRAY,BACKGROUND);
    fill(fb,12,62,456,148,GRAY);fill(fb,13,63,454,146,PANEL);
}
static void clear_input(void) {
    for(unsigned y=0;y<ROWS;++y)for(unsigned x=0;x<COLS;++x)input[y][x]=' ';
    row=column=0;
}
static void newline(void) {
    column=0;
    if(++row==ROWS) {
        for(unsigned y=1;y<ROWS;++y)for(unsigned x=0;x<COLS;++x)input[y-1][x]=input[y][x];
        for(unsigned x=0;x<COLS;++x)input[ROWS-1][x]=' ';
        row=ROWS-1;
    }
}
static char key_character(unsigned usage,unsigned mods,unsigned lock) {
    if(mods&0xddu)return 0; /* Ctrl, Alt or GUI: display state without inserting text. */
    unsigned shift=!!(mods&0x22u);
    if(usage>=4 && usage<=29)return (char)((shift^lock?'A':'a')+usage-4);
    if(usage>=30 && usage<=39)return (shift?"!@#$%^&*()":"1234567890")[usage-30];
    if(usage==44)return ' ';
    if(usage==45)return shift?'_':'-';
    if(usage==46)return shift?'+':'=';
    if(usage==47)return shift?'{':'[';
    if(usage==48)return shift?'}':']';
    if(usage==49)return shift?'|':'\\';
    if(usage==51)return shift?':':';';
    if(usage==52)return shift?'"':'\'';
    if(usage==53)return shift?'~':'`';
    if(usage==54)return shift?'<':',';
    if(usage==55)return shift?'>':'.';
    if(usage==56)return shift?'?':'/';
    return 0;
}
static void key_event(const hal_usb_key_t *key) {
    last_usage=key->usage;last_pressed=key->pressed;modifiers=key->modifiers;
    if(key->pressed)++key_down;else ++key_up;
    ++revision;
    if(!key->pressed)return;
    if(key->usage==57) {caps^=1;return;}
    if(key->modifiers&0xddu)return;
    if(key->usage==41) {clear_input();return;}
    if(key->usage==40) {newline();return;}
    if(key->usage==42) {
        if(column)--column;else if(row) {--row;column=COLS-1;}
        input[row][column]=' ';return;
    }
    char ch=key_character(key->usage,key->modifiers,caps);
    if(ch) {input[row][column++]=ch;if(column==COLS)newline();}
}
static void append(char *s,unsigned *n,const char *value) {
    while(*value && *n<STATUS_COLS)s[(*n)++]=*value++;
}
static void number(char *s,unsigned *n,int value) {
    if(value<0) {append(s,n,"-");value=-value;}
    char digits_reversed[10];unsigned count=0;
    do {digits_reversed[count++]=(char)('0'+value%10);value/=10;}while(value);
    while(count && *n<STATUS_COLS)s[(*n)++]=digits_reversed[--count];
}
static void hexbyte(char *s,unsigned *n,unsigned value) {
    static const char hex[]="0123456789ABCDEF";
    char pair[3]={hex[(value>>4)&15],hex[value&15],0};append(s,n,pair);
}
static void make_status(char lines[3][STATUS_COLS],const hal_usb_info_t *info) {
    for(unsigned i=0;i<3;++i)for(unsigned j=0;j<STATUS_COLS;++j)lines[i][j]=' ';
    unsigned n=0;
    append(lines[0],&n,"MOUSE X:");number(lines[0],&n,mouse_x);append(lines[0],&n," Y:");number(lines[0],&n,mouse_y);
    append(lines[0],&n," BUTTONS:");hexbyte(lines[0],&n,buttons);append(lines[0],&n," WHEEL:");number(lines[0],&n,wheel);
    n=0;append(lines[1],&n,"KEY:");hexbyte(lines[1],&n,last_usage);append(lines[1],&n,last_pressed?" DOWN":" UP");
    append(lines[1],&n," MOD:");hexbyte(lines[1],&n,modifiers);append(lines[1],&n,caps?" CAPS:ON":" CAPS:OFF");
    append(lines[1],&n," DOWN:");number(lines[1],&n,(int)key_down);append(lines[1],&n," UP:");number(lines[1],&n,(int)key_up);
    n=0;append(lines[2],&n,info->connected?"USB CONNECTED":"USB WAITING");
    append(lines[2],&n," HID:");number(lines[2],&n,(int)info->hid_interfaces);
    append(lines[2],&n," ERR:");number(lines[2],&n,(int)info->errors);
    append(lines[2],&n," DROP:");number(lines[2],&n,(int)(info->key_drops+info->mouse_drops+info->report_drops));
    append(lines[2],&n," LCD:");number(lines[2],&n,(int)rgb_lcd_underflows_read());
}
static void restore_cursor(unsigned slot) {
    if(!cursor[slot].drawn)return;
    volatile uint16_t *fb=hal_video_frame(slot);
    for(int y=0;y<18 && cursor[slot].y+y<HEIGHT;++y)for(int x=0;x<12 && cursor[slot].x+x<WIDTH;++x)
        fb[(cursor[slot].y+y)*WIDTH+cursor[slot].x+x]=cursor[slot].pixels[y*12+x];
}
static void draw_cursor(unsigned slot) {
    /* W=white outline, X=button-colored fill, space=transparent. */
    static const char shape[18][13]={
        "W           ","WW          ","WXW         ","WXXW        ","WXXXW       ","WXXXXW      ",
        "WXXXXXW     ","WXXXXXXW    ","WXXXXXXXW   ","WXXXXXXXXW  ","WXXXXXXXXXW ","WXXXXWWWWWWW",
        "WXXWXW      ","WXW WXW     ","WW  WXW     ","W    WXW    ","     WXW    ","      WW    "};
    volatile uint16_t *fb=hal_video_frame(slot);
    uint16_t color=buttons&1?0xf800:buttons&2?0x07e0:buttons&4?0x001f:0;
    for(int y=0;y<18 && mouse_y+y<HEIGHT;++y)for(int x=0;x<12 && mouse_x+x<WIDTH;++x) {
        unsigned pixel=(mouse_y+y)*WIDTH+mouse_x+x;
        cursor[slot].pixels[y*12+x]=fb[pixel];
        if(shape[y][x]!=' ')fb[pixel]=shape[y][x]=='W'?WHITE:color;
    }
    cursor[slot].x=mouse_x;cursor[slot].y=mouse_y;cursor[slot].drawn=1;
}
static void render(const hal_usb_info_t *info) {
    unsigned slot=active^1u;volatile uint16_t *fb=hal_video_frame(slot);
    restore_cursor(slot);
    for(unsigned y=0;y<ROWS;++y)for(unsigned x=0;x<COLS;++x) {
        char ch=(y==row && x==column)?'_':input[y][x];
        if(painted[slot][y][x]!=ch) {
            cell(fb,24+x*CW,66+y*CH,ch,2,WHITE,PANEL);painted[slot][y][x]=ch;
        }
    }
    char lines[3][STATUS_COLS];make_status(lines,info);
    for(unsigned y=0;y<3;++y)for(unsigned x=0;x<STATUS_COLS;++x)if(status_painted[slot][y][x]!=lines[y][x]) {
        cell(fb,12+x*6,220+y*16,lines[y][x],1,y==2?CYAN:WHITE,BACKGROUND);
        status_painted[slot][y][x]=lines[y][x];
    }
    draw_cursor(slot);
    if(hal_video_present(slot)!=HAL_OK) {++failures;hal_uart_puts("DEMO frame swap FAIL\r\n");return;}
    active=slot;rendered=revision;++renders;
}
static void status(void) {
    hal_usb_info_t info;hal_usb_get_info(&info);
    hal_uart_puts("DEMO mouse x=");hal_uart_hex(mouse_x);hal_uart_puts(" y=");hal_uart_hex(mouse_y);
    hal_uart_puts(" buttons=");hal_uart_hex(buttons);hal_uart_puts(" wheel=");hal_uart_hex((uint32_t)wheel);
    hal_uart_puts("\r\nKEY down=");hal_uart_hex(key_down);hal_uart_puts(" up=");hal_uart_hex(key_up);
    hal_uart_puts(" usage=");hal_uart_hex(last_usage);hal_uart_puts(" modifiers=");hal_uart_hex(modifiers);
    hal_uart_puts(" caps=");hal_uart_hex(caps);hal_uart_puts(" mouse_events=");hal_uart_hex(mouse_events);
    hal_uart_puts("\r\nUSB reports=");hal_uart_hex(info.reports);hal_uart_puts(" errors=");hal_uart_hex(info.errors);
    hal_uart_puts(" key_drops=");hal_uart_hex(info.key_drops);hal_uart_puts(" mouse_drops=");hal_uart_hex(info.mouse_drops);
    hal_uart_puts(" report_drops=");hal_uart_hex(info.report_drops);hal_uart_puts(" hid=");hal_uart_hex(info.hid_interfaces);
    hal_uart_puts("\r\nDEMO renders=");hal_uart_hex(renders);hal_uart_puts(" failures=");hal_uart_hex(failures);
    hal_uart_puts("\r\nTEXT BEGIN\r\n");
    for(unsigned y=0;y<ROWS;++y) {
        unsigned end=COLS;while(end && input[y][end-1]==' ')--end;
        for(unsigned x=0;x<end;++x)hal_uart_putc(input[y][x]);
        hal_uart_puts("\r\n");
    }
    hal_uart_puts("TEXT END\r\n");hal_video_status();
}
static int equal(const char *a,const char *b) {while(*a && *a==*b) {++a;++b;}return *a==*b;}
static void command(const char *s) {
    if(equal(s,"status"))status();
    else if(equal(s,"test demo")) {
        hal_usb_info_t info;hal_usb_get_info(&info);hal_stats_t stats;hal_get_stats(&stats);
        unsigned before=rgb_lcd_completed_read();hal_delay_ms(40);
        int ok=info.connected && info.hid_interfaces && !info.errors && !info.key_drops && !info.mouse_drops
            && !info.report_drops && !failures && !rgb_lcd_underflows_read() && before!=rgb_lcd_completed_read()
            && !stats.uart_drops && !stats.unhandled_irqs && mouse_x>=0 && mouse_x<WIDTH && mouse_y>=0 && mouse_y<HEIGHT
            && key_character(4,0,0)=='a' && key_character(4,2,0)=='A' && key_character(4,0,1)=='A'
            && key_character(4,2,1)=='a' && key_character(4,1,0)==0 && key_character(30,2,0)=='!';
        status();hal_uart_puts(ok?"TEST demo PASS\r\n":"TEST demo FAIL\r\n");
    } else if(equal(s,"reboot"))hal_reboot();
    else hal_uart_puts("Commands: status, test demo, reboot (!). Keyboard Esc clears text.\r\n");
    hal_uart_puts("> ");
}
int main(void) {
    hal_init();clear_input();
    if(hal_video_init()!=HAL_OK) {hal_uart_puts("DEMO LCD init FAIL\r\n");for(;;) {if(hal_uart_getc()=='!')hal_reboot();}}
    background(1);draw_cursor(1);
    if(hal_video_present(1)!=HAL_OK)++failures;
    active=1;background(0);draw_cursor(0);
    hal_uart_puts("USB INPUT DEMO 480x272 RGB565\r\nSYSTEM READY\r\n> ");
    char line[40];unsigned length=0;uint32_t next_frame=hal_time_ms(),next_status=next_frame;
    for(;;) {
        hal_poll();hal_usb_key_t key;hal_usb_mouse_t mouse;hal_usb_report_t raw;
        while(hal_usb_key_take(&key)==HAL_OK)key_event(&key);
        while(hal_usb_mouse_take(&mouse)==HAL_OK) {
            mouse_x+=mouse.x;mouse_y+=mouse.y;buttons=mouse.buttons;
            if(mouse_x<0)mouse_x=0;
            if(mouse_x>=WIDTH)mouse_x=WIDTH-1;
            if(mouse_y<0)mouse_y=0;
            if(mouse_y>=HEIGHT)mouse_y=HEIGHT-1;
            /* Bounded displayed accumulator avoids signed overflow in long sessions. */
            wheel+=mouse.wheel;if(wheel>999999)wheel=999999;if(wheel< -999999)wheel= -999999;
            ++mouse_events;++revision;
        }
        while(hal_usb_report_take(&raw)==HAL_OK) {}
        int ch=hal_uart_getc();if(ch=='!')hal_reboot();
        if(ch=='\r' || ch=='\n') {if(length) {line[length]=0;command(line);length=0;}}
        else if(ch==8 || ch==127) {if(length)--length;}
        else if(ch>=32 && ch<127 && length<sizeof(line)-1)line[length++]=(char)ch;
        uint32_t now=hal_time_ms();
        if(hal_deadline_reached(now,next_status)) {++revision;next_status=now+500;}
        if(rendered!=revision && hal_deadline_reached(now,next_frame)) {
            hal_usb_info_t info;hal_usb_get_info(&info);render(&info);next_frame=hal_time_ms()+16;
        }
    }
}
