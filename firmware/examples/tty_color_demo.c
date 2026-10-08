/* TTY colour demo: a BIOS payload that exercises the resident VT100/ANSI
 * terminal with the full 16-colour SGR set, attributes and cursor addressing.
 *
 *   .venv/Scripts/python.exe scripts/bios_payload.py \
 *       --source firmware/examples/tty_color_demo.c --output-dir build/tty-color
 *   # then in the BIOS:  set server <host-ip> ; boot net BOOT.RPB
 *
 * The panel is 80x34 cells of 6x8 pixels.  Everything goes through the BIOS
 * console, so the same byte stream renders correctly on a host terminal
 * attached to UART as well - useful for checking the demo without the LCD.
 * Shows the palette, waits for a key (or about 10 s), restores text mode and
 * returns to the BIOS. */
#include "../bios/include/bios.h"

static void out(const char *s,unsigned n) {bios_call(BIOS_WRITE,(uintptr_t)s,n,0,0);}
static void puts_(const char *s) {unsigned n=0;while(s[n])++n;out(s,n);}
static void put_dec(unsigned v) {char b[10];unsigned n=0;do {b[n++]=(char)('0'+v%10);v/=10;}while(v);while(n)out(&b[--n],1);}
static void sgr(unsigned code) {puts_("\033[");put_dec(code);out("m",1);}
static void cup(unsigned row,unsigned col) {puts_("\033[");put_dec(row);out(";",1);put_dec(col);out("H",1);}
static void pump(void) {bios_call(BIOS_POLL,0,0,0,0);}
static void repeat_ch(char c,unsigned n) {
    static char line[64];
    while(n) {unsigned k=n>sizeof line?sizeof line:n;for(unsigned i=0;i<k;++i)line[i]=c;out(line,k);n-=k;}
}
static void poll_ms(unsigned ms) {
    uint32_t start=(uint32_t)bios_call(BIOS_TIME,0,0,0,0);
    while((uint32_t)(bios_call(BIOS_TIME,0,0,0,0)-start)<ms) {
        pump();
        if(bios_call(BIOS_GETC,0,0,0,0)>=0)return;   /* any key skips the wait */
    }
}

/* 三个字符的名称，方便按列排版 */
static const char *const names[16]={
    "BLK","RED","GRN","YEL","BLU","MAG","CYN","WHT",
    "GRY","LRD","LGR","LYE","LBL","LMA","LCY","LWH"};

int payload_main(const struct bios_info *info) {
    (void)info;

    /* 1) 标题：粗体 + 亮白 */
    puts_("\033[2J\033[H");
    sgr(1);sgr(97);puts_("TTY COLOUR DEMO  -  BIOS VT100/ANSI terminal");
    sgr(0);puts_("\r\n80x34 cells, 16 colours, SGR 30-37/90-97 and 40-47/100-107\r\n");
    pump();

    /* 2) 标准前景 30-37 与亮色前景 90-97 */
    puts_("\r\n");sgr(1);puts_("standard foreground 30-37");sgr(0);puts_("\r\n  ");
    for(unsigned i=0;i<8;++i) {sgr(30+i);puts_(names[i]);out(" ",1);}
    sgr(0);
    puts_("\r\n");sgr(1);puts_("bright foreground 90-97");sgr(0);puts_("\r\n  ");
    for(unsigned i=0;i<8;++i) {sgr(90+i);puts_(names[8+i]);out(" ",1);}
    sgr(0);
    pump();

    /* 3) 标准背景 40-47 与亮色背景 100-107（用空格色块显示） */
    puts_("\r\n\r\n");sgr(1);puts_("standard background 40-47");sgr(0);puts_("\r\n  ");
    for(unsigned i=0;i<8;++i) {sgr(30+i);sgr(40+i);repeat_ch(' ',4);}
    sgr(0);
    puts_("\r\n");sgr(1);puts_("bright background 100-107");sgr(0);puts_("\r\n  ");
    for(unsigned i=0;i<8;++i) {sgr(30+i);sgr(100+i);repeat_ch(' ',4);}
    sgr(0);
    pump();

    /* 4) 属性：正常 / 粗体 / 反显 / 粗体+反显 */
    puts_("\r\n\r\n");sgr(1);puts_("attributes");sgr(0);puts_("\r\n  ");
    puts_("normal  ");repeat_ch(' ',10);
    sgr(1);puts_("  bold  ");repeat_ch(' ',10);
    sgr(0);sgr(7);puts_("  reverse  ");repeat_ch(' ',10);
    sgr(0);sgr(1);sgr(7);puts_("  bold+reverse  ");repeat_ch(' ',10);
    sgr(0);

    /* 5) 粗体会把 1-7 提亮到 9-15，两行应当明显不同 */
    puts_("\r\n\r\n");sgr(1);puts_("bold promotes 1-7 to 9-15");sgr(0);
    puts_("\r\n  plain  ");
    for(unsigned i=1;i<8;++i) {sgr(30+i);repeat_ch('#',4);}
    puts_("\r\n  bold   ");
    for(unsigned i=1;i<8;++i) {sgr(1);sgr(30+i);repeat_ch('#',4);}
    sgr(0);
    pump();

    /* 6) 光标定位：用 CUP 逐行画一个框 */
    puts_("\r\n\r\n");sgr(1);puts_("CSI row;col addressing");sgr(0);
    cup(21,6); out("+",1);repeat_ch('-',52);out("+",1);
    cup(22,6); out("|",1);cup(22,59);out("|",1);
    cup(23,6); out("+",1);repeat_ch('-',52);out("+",1);
    cup(22,9); sgr(96);puts_("this frame was placed with ESC[row;colH");sgr(0);

    /* 7) 底色渐变条 */
    cup(26,3); sgr(1);puts_("gradient ");sgr(0);
    for(unsigned i=0;i<16;++i) {sgr(40+i);sgr(30+((i+1)&7));out("   ",3);}
    sgr(0);
    pump();

    /* 8) 提示并等待 */
    cup(29,3); sgr(7);puts_(" press any key to return to BIOS ");sgr(0);
    poll_ms(10000);

    /* 9) 收尾：清屏后留三行已知颜色的小样。不清屏是为了让这几行的属性留在
     * TTY 网格里，可以用 BIOS 的 `md TTY` 读回来核对 fg/bg 索引。 */
    sgr(0);puts_("\033[2J\033[H");
    sgr(31);puts_("fg31 red\r\n");
    sgr(32);puts_("fg32 green\r\n");
    sgr(34);sgr(47);puts_("fg34 bg47\r\n");
    sgr(0);
    bios_call(BIOS_VIDEO_MODE,BIOS_TEXT,0,0,0);
    return 0;
}
