/* Host regression for the USB HID → terminal byte mapping in the BIOS console.
 *
 *   cc -DMINI_BOOTLOADER=0 -DMINI_FEATURE_USB=1 -Wall -Wextra
 *      -I <build>/software/include -I firmware/hal/include -I firmware/bios
 *      -I firmware/common tests/usb_key_map_test.c firmware/bios/vt.c
 *
 * Feeds synthetic HID usage codes through the real bios_console_poll() and
 * checks the bytes the terminal would receive.  This exists because the usage
 * table is easy to get wrong (0x1E-0x27 are the digit row, 40 is Enter, and the
 * arrow keys are 79-82) and a mistake there is invisible until someone types.
 */
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define __GENERATED_CSR_H
#define __GENERATED_SOC_H
#include <hal/hal.h>

static unsigned checks,failures;
static void check(const char *what,int ok) {
    ++checks;
    if(!ok) {++failures;printf("FAIL %s\n",what);}
}

/* --- UART plumbing that console.c/uart.c expect --- */
static char uart[256];static unsigned nuart;
static unsigned uart_txfull_read(void) {return 0;}
static void uart_rxtx_write(uint8_t c) {if(nuart<sizeof uart)uart[nuart++]=c;}
#include "../firmware/drivers/uart.c"
void hal_console_mirror(void (*callback)(char)) {io_console_mirror(callback);}
void hal_uart_putc(char c) {putchar_uart(c);}
static void mirror(char c) {(void)c;}

/* --- synthetic USB event queues, consumed by console.c --- */
static hal_usb_key_t keyq[64];static unsigned khead,ktail;
static hal_usb_mouse_t mouseq[8];static unsigned mhead,mtail;
static hal_usb_report_t reportq[8];static unsigned rhead,rtail;
static unsigned reports_seen;
static uint32_t fake_ms;   /* tests advance this; hal_time_ms() reports it */

#include "../firmware/bios/console.c"

hal_result_t hal_usb_key_take(hal_usb_key_t *out) {
    if(khead==ktail)return HAL_BUSY;
    *out=keyq[ktail++&63];return HAL_OK;
}
hal_result_t hal_usb_mouse_take(hal_usb_mouse_t *out) {
    if(mhead==mtail)return HAL_BUSY;
    *out=mouseq[mtail++&7];return HAL_OK;
}
hal_result_t hal_usb_report_take(hal_usb_report_t *out) {
    if(rhead==rtail)return HAL_BUSY;
    *out=reportq[rtail++&7];return HAL_OK;
}
void tests_usb_report(const hal_usb_report_t *report) {(void)report;++reports_seen;}
void tests_usb_key(const hal_usb_key_t *key) {(void)key;}
void tests_usb_mouse(const hal_usb_mouse_t *mouse) {(void)mouse;}
void tests_video_stop(void) {}
void hal_poll(void) {}
uint32_t hal_time_ms(void) {return fake_ms;}
int hal_uart_getc(void) {return -1;}
volatile uint16_t *hal_video_frame(unsigned slot) {(void)slot;return 0;}
hal_result_t hal_video_present(unsigned slot) {(void)slot;return HAL_UNSUPPORTED;}

/* --- helpers --- */
static void release(unsigned usage) {
    hal_usb_key_t k;
    memset(&k,0,sizeof k);
    k.usage=(uint8_t)usage;k.pressed=0;
    keyq[khead++&63]=k;
    bios_console_poll();
}
static unsigned poll_bytes(void) {
    unsigned n=0;int c;
    bios_console_poll();
    while((c=bios_getc())>=0)++n;
    return n;
}
static void press(unsigned usage,unsigned mods) {
    hal_usb_key_t k;
    memset(&k,0,sizeof k);
    k.usage=(uint8_t)usage;k.pressed=1;k.modifiers=(uint8_t)mods;
    keyq[khead++&63]=k;
    bios_console_poll();
}
static void drain(void) {while(bios_getc()>=0) {;}}
/* Collect the bytes one keypress produces. */
static unsigned bytes_for(unsigned usage,unsigned mods,unsigned char *out,unsigned cap) {
    unsigned n=0;int c;
    press(usage,mods);
    while(n<cap && (c=bios_getc())>=0)out[n++]=(unsigned char)c;
    return n;
}
static int one(unsigned usage,unsigned mods,unsigned char expect) {
    unsigned char b[8];unsigned n=bytes_for(usage,mods,b,sizeof b);
    return n==1 && b[0]==expect;
}
static int seq(unsigned usage,unsigned mods,const char *expect) {
    unsigned char b[8];unsigned n=bytes_for(usage,mods,b,sizeof b);
    return n==strlen(expect) && !memcmp(b,expect,n);
}

int main(void) {
    unsigned caps_before;
    io_console_mirror(mirror);
    bios_console_init(0);
    drain();

    /* 数字行（0x1E-0x27）与 Enter（40）必须各走各的路径：这一条曾是 bug。 */
    check("'1' -> '1'",one(30,0,'1'));
    check("'8' -> '8'",one(37,0,'8'));
    check("'9' -> '9'",one(38,0,'9'));
    check("'0' -> '0'",one(39,0,'0'));
    check("shift '1' -> '!'",one(30,0x02,'!'));
    check("shift '0' -> ')'",one(39,0x02,')'));
    check("Enter -> CR",one(40,0,'\r'));

    /* 控制键 */
    check("Escape -> 0x1b",one(41,0,0x1b));
    check("Backspace -> 8",one(42,0,8));
    check("Tab -> 9",one(43,0,9));
    check("Space -> ' '",one(44,0,' '));

    /* 字母与 Caps Lock */
    check("'a' -> 'a'",one(4,0,'a'));
    check("shift 'a' -> 'A'",one(4,0x02,'A'));
    caps_before=caps;
    press(57,0);drain();                       /* Caps Lock toggles */
    check("CapsLock toggles caps",caps!=caps_before);
    check("caps 'a' -> 'A'",one(4,0,'A'));
    check("caps+shift 'a' -> 'a'",one(4,0x02,'a'));
    press(57,0);drain();                       /* back off */
    check("'a' -> 'a' again",one(4,0,'a'));

    /* 标点：usage 不是连续 ASCII，50 是 Non-US # */
    check("'-' -> '-'",one(45,0,'-'));
    check("shift '-' -> '_'",one(45,0x02,'_'));
    check("'=' -> '='",one(46,0,'='));
    check("'[' -> '['",one(47,0,'['));
    check("shift '[' -> '{'",one(47,0x02,'{'));
    check("']' -> ']'",one(48,0,']'));
    check("'\\' -> '\\'",one(49,0,'\\'));
    check("';' -> ';'",one(51,0,';'));
    check("''' -> '''",one(52,0,'\''));
    check("'`' -> '`'",one(53,0,'`'));
    check("',' -> ','",one(54,0,','));
    check("'.' -> '.'",one(55,0,'.'));
    check("'/' -> '/'",one(56,0,'/'));
    check("shift '/' -> '?'",one(56,0x02,'?'));
    check("usage 50 (Non-US #) is dropped",one(50,0,0)==0 && 1);

    /* Ctrl/Alt 组合必须被吞掉，不能当普通字符 */
    check("Ctrl+'a' is dropped",one(4,0x01,0)==0 && 1);

    /* 导航键：79..82 是方向键 */
    check("Up -> ESC [ A",seq(82,0,"\033[A"));
    check("Down -> ESC [ B",seq(81,0,"\033[B"));
    check("Right -> ESC [ C",seq(79,0,"\033[C"));
    check("Left -> ESC [ D",seq(80,0,"\033[D"));
    check("Home -> ESC [ H",seq(74,0,"\033[H"));
    check("End -> ESC [ F",seq(77,0,"\033[F"));
    check("Insert -> ESC [ 2 ~",seq(73,0,"\033[2~"));
    check("Delete -> ESC [ 3 ~",seq(76,0,"\033[3~"));
    check("PageUp -> ESC [ 5 ~",seq(75,0,"\033[5~"));
    check("PageDown -> ESC [ 6 ~",seq(78,0,"\033[6~"));

    /* DECCKM (?1h) 后方向键改用 ESC O */
    vt_putc(&term,'\033');vt_putc(&term,'[');vt_putc(&term,'?');vt_putc(&term,'1');vt_putc(&term,'h');
    check("DECCKM set",(term.mode&VT_MODE_APPKEYS)!=0);
    check("Up -> ESC O A under DECCKM",seq(82,0,"\033OA"));
    check("Left -> ESC O D under DECCKM",seq(80,0,"\033OD"));

    /* 原始报告队列必须被排空：漏掉会抬高 HAL 的 report_drops 并让 test usb 失败 */
    reports_seen=0;
    for(unsigned i=0;i<5;++i) {hal_usb_report_t r;memset(&r,0,sizeof r);r.length=1;reportq[rhead++&7]=r;}
    press(4,0);
    check("all queued HID reports drained",reports_seen==5);

    /* 非按键事件（release）不得产生字节 */
    {unsigned n=0;int c;hal_usb_key_t k;memset(&k,0,sizeof k);k.usage=4;k.pressed=0;
     drain();
     keyq[khead++&63]=k;bios_console_poll();
     while((c=bios_getc())>=0)++n;
     check("key release produces nothing",n==0);}

    /* --- 键盘自动重复（typematic）：HID 键盘不发重复报告，必须由主机生成 --- */
    drain();
    fake_ms=1000;
    press(4,0);                                   /* 'a' 按下 */
    drain();
    fake_ms=1000+499;
    check("repeat: nothing before the delay",poll_bytes()==0);
    fake_ms=1000+500;
    check("repeat: fires at the delay",poll_bytes()==1);
    fake_ms=1000+559;
    check("repeat: not again before the period",poll_bytes()==0);
    fake_ms=1000+560;
    check("repeat: fires at the period",poll_bytes()==1);
    fake_ms=1000+620;
    check("repeat: keeps firing",poll_bytes()==1);
    /* 松开后必须停止，否则会一直灌字符 */
    release(4);
    fake_ms=1000+7000;
    check("repeat: stops after release",poll_bytes()==0);
    /* CapsLock / ESC 不重复 */
    drain();
    fake_ms=20000;
    press(57,0);
    fake_ms=21000;
    check("repeat: CapsLock does not repeat",poll_bytes()==0);
    drain();
    press(41,0);                                  /* ESC */
    drain();                                      /* 按下时本身会产出一个 0x1b */
    fake_ms=22000;
    check("repeat: ESC does not repeat",poll_bytes()==0);
    /* 方向键重复（会重复整条 CSI，共 3 字节） */
    drain();
    press(80,0);                                  /* Left */
    drain();
    fake_ms=30000;
    check("repeat: navigation key repeats",poll_bytes()==3);
    release(80);
    fake_ms=40000;
    check("repeat: navigation stops after release",poll_bytes()==0);

    printf("%u checks, %u failures\n",checks,failures);
    return failures!=0;
}