// BIOS TTY：VT100/ANSI 终端（vt.c）+ 双路输出（UART 透传 / LCD 网格镜像）+ 输入队列。
#include "internal.h"
#include "vt.h"
#include "../diagnostics/tests.h"
#include <hal/hal.h>
#include "../common/rgb_canvas.h"
#include <string.h>

enum {COLS=VT_COLS,ROWS=VT_ROWS};
static vt_t term;
static vt_grid_t drawn;
static unsigned slot,video,mode,dirty=1,next_render,load_started,load_next;
static uint8_t queue[128];
static unsigned head,tail;
#if MINI_FEATURE_USB
static unsigned caps;
static struct {int x,y,wheel,buttons;uint32_t time_ms;unsigned pending;} mouse;
#endif
static void enqueue(unsigned char ch) {unsigned next=(head+1)&127;if(next!=tail){queue[head++]=ch;head&=127;}}

#if MINI_FEATURE_USB
static int32_t accumulate(int32_t value,int delta) {value+=delta;return value>32767?32767:value<-32767?-32767:value;}

/* HID 键盘 usage（usage page 0x07）。这套编号不是 ASCII 也不是字母序，
 * 容易记错：0x28=40 是 Enter，而方向键是 79-82。原版 console.c 用的就是这套。 */
enum {
  KEY_ENTER=40, KEY_ESC=41, KEY_BACKSPACE=42, KEY_TAB=43, KEY_SPACE=44,
  KEY_CAPSLOCK=57,
  KEY_INSERT=73, KEY_HOME=74, KEY_PAGEUP=75, KEY_DELETE=76,
  KEY_END=77, KEY_PAGEDOWN=78,
  KEY_RIGHT=79, KEY_LEFT=80, KEY_DOWN=81, KEY_UP=82,
};

// 导航键 usage → 终端字节序列（方向键按 DECCKM 选 ESC O / ESC [）。
static void key_bytes(unsigned k) {
  if(k>=KEY_RIGHT && k<=KEY_UP) {   // 79..82 = 右左下上
    char arrow=(k==KEY_UP)?'A':(k==KEY_DOWN)?'B':(k==KEY_RIGHT)?'C':'D';
    enqueue(0x1b);enqueue(term.mode&VT_MODE_APPKEYS?'O':'[');enqueue(arrow);return;
  }
  if(k==KEY_HOME) {enqueue(0x1b);enqueue('[');enqueue('H');return;}
  if(k==KEY_END) {enqueue(0x1b);enqueue('[');enqueue('F');return;}
  if(k==KEY_INSERT) {enqueue(0x1b);enqueue('[');enqueue('2');enqueue('~');return;}
  if(k==KEY_DELETE) {enqueue(0x1b);enqueue('[');enqueue('3');enqueue('~');return;}
  if(k==KEY_PAGEUP) {enqueue(0x1b);enqueue('[');enqueue('5');enqueue('~');return;}
  if(k==KEY_PAGEDOWN) {enqueue(0x1b);enqueue('[');enqueue('6');enqueue('~');return;}
}

static unsigned char key_char(unsigned k,unsigned mods) {
  /* shift 必须归一到 0/1 再与 caps 异或：`mods&0x22` 本身是 0x02/0x20，
   * 直接异或 caps 会得到 3 这种非零值，CapsLock 下按 Shift 反而还是大写。 */
  unsigned shift=!!(mods&0x22);
  if(k==KEY_ESC) return 0x1b;
  if(mods&0xcc) return 0;  // Alt/GUI 不作为普通字符送入终端
  if(mods&0x11) return k>=4 && k<=29 ? (unsigned char)(k-3) : 0; // Ctrl-A..Z
  if(k>=4 && k<=29) return (unsigned char)((shift^caps?65:97)+k-4);
  if(k>=30 && k<=39) return (unsigned char)(shift?"!@#$%^&*()"[k-30]:"1234567890"[k-30]);
  if(k==KEY_ENTER) return '\r';
  if(k==KEY_BACKSPACE) return 8;
  if(k==KEY_TAB) return '\t';
  if(k==KEY_SPACE) return ' ';
  /* 标点也用 usage，不是连续 ASCII 序：50(0x32) 是 Non-US #，这里直接跳过。 */
  static const uint8_t codes[]={45,46,47,48,49,51,52,53,54,55,56};
  static const char plain[]="-=[]\\;'`,./";
  static const char shifted[]="_+{}|:\"~<>?";
  for(unsigned i=0;i<sizeof(codes);++i)
    if(k==codes[i]) return (unsigned char)(shift?shifted[i]:plain[i]);
  return 0;
}

/* 键盘自动重复（typematic）。HID 启动协议键盘只在按键状态变化时发报告，
 * 按住不放不会再来报告，所以重复必须由主机生成：首次延迟后按固定周期重发。 */
enum { KEY_REPEAT_DELAY_MS=500, KEY_REPEAT_PERIOD_MS=60 };
static unsigned repeat_usage,repeat_mods;
static unsigned repeat_held;
static uint32_t repeat_next;

static int key_repeatable(unsigned k) {
  return k!=KEY_CAPSLOCK && k!=KEY_ESC;   /* 锁定键和 ESC 不重复 */
}

/* 按当前记录的重发状态，到点就再发一次。返回是否发送。 */
static void key_repeat_poll(void) {
  if(!repeat_held)return;
  uint32_t now=hal_time_ms();
  if(!hal_deadline_reached(now,repeat_next))return;
  repeat_next=now+KEY_REPEAT_PERIOD_MS;
  if(repeat_usage>=KEY_INSERT && repeat_usage<=KEY_UP)key_bytes(repeat_usage);
  else {unsigned char c=key_char(repeat_usage,repeat_mods);if(c)enqueue(c);}
}
#endif

/* `md ... u` 用：暂时静默 LCD 镜像，dump 只走 UART。
 * 字节仍然照常写到 UART（mirror 是在串口写完之后才被调用的），
 * 只是不再进 TTY 网格，所以不会把文字画面滚掉。 */
static int mirror_muted;
void bios_console_uart_only(int on) {mirror_muted=on?1:0;}

static void terminal_putc(char ch) {
  if(mirror_muted)return;
  vt_putc(&term,(unsigned char)ch);
  dirty=1;
}

// 光标以反显块绘制：渲染时覆盖 (cx,cy) 格，不改动网格内容。
// 光标移动/属性变化都会让该格在 drawn 与 grid 间不一致，从而自动重绘。
static void paint_cursor(volatile uint16_t *fb) {
  if(!(term.mode&VT_MODE_CURSOR))return;
  uint16_t fg,bg;
  vt_colors(&term.grid[term.cy][term.cx],&fg,&bg);
  cell(fb,term.cx*6,term.cy*8,term.grid[term.cy][term.cx].ch,1,bg,fg);
}

static void vt_echo(void *ctx, const char *text) {
  (void)ctx;
  while(*text)enqueue((unsigned char)*text++);
}

void bios_console_init(unsigned lcd_ok) {
  video=(lcd_ok&&hal_video_frame(0))?1:0;
  vt_reset(&term);
  term.mode=VT_MODE_CURSOR;
  term.report=vt_echo;
  dirty=1;
  hal_console_mirror(terminal_putc);
}

void bios_putc(char ch) {hal_uart_putc(ch);}
void bios_puts(const char *text) {while(*text)bios_putc(*text++);}
void bios_hex(uint32_t value) {const char *d="0123456789abcdef";for(int i=28;i>=0;i-=4)bios_putc(d[(value>>i)&15]);}
void bios_decimal(uint32_t value) {char b[10];int n=0;do{b[n++]='0'+value%10;value/=10;}while(value);while(n)bios_putc(b[--n]);}

int bios_getc(void) {
  if(tail==head)return -1;
  unsigned char ch=queue[tail++];tail&=127;
  return ch;
}

/* 诊断用：返回当前活动网格基址。每个单元 4 字节（ch 在低字节、attr 在其后）。 */
uintptr_t bios_console_grid(void) {return (uintptr_t)term.grid;}

int bios_mouse_take(struct bios_mouse *event) {
#if MINI_FEATURE_USB
  if(!mouse.pending)return -1;
  event->x=mouse.x;event->y=mouse.y;event->buttons=mouse.buttons;
  event->wheel=mouse.wheel;event->time_ms=mouse.time_ms;
  mouse.pending=0;mouse.x=mouse.y=mouse.wheel=0;
  return 0;
#else
  (void)event;return -1;
#endif
}

int bios_video_mode(unsigned m) {
  if(m>1||!video)return -1;
  mode=m;
  if(!mode) {tests_video_stop();dirty=1;}
  return 0;
}

void bios_console_poll(void) {
  int ch;
  while((ch=hal_uart_getc())>=0)enqueue((unsigned char)ch);
#if MINI_FEATURE_USB
  hal_usb_key_t key;
  while(hal_usb_key_take(&key)==HAL_OK) {
    if(key.usage>=0xe0 && key.usage<=0xe7) {
      // 修饰键变化不替换正在重复的普通键；按住 W 松开 Ctrl 后应重复 w。
      repeat_mods=key.modifiers;
      continue;
    }
    if(!key.pressed) {                       /* 松开：停止自动重复 */
      if(key.usage==repeat_usage)repeat_held=0;
      continue;
    }
    if(key.usage==KEY_CAPSLOCK){caps^=1;repeat_held=0;continue;}
    if(key.usage>=KEY_INSERT && key.usage<=KEY_UP)key_bytes(key.usage);
    else {unsigned char c=key_char(key.usage,key.modifiers);if(c)enqueue(c);}
    if(key_repeatable(key.usage)) {           /* 记录本次按键，启动 typematic */
      repeat_usage=key.usage;repeat_mods=key.modifiers;repeat_held=1;
      repeat_next=hal_time_ms()+KEY_REPEAT_DELAY_MS;
    } else repeat_held=0;
  }
  key_repeat_poll();
  hal_usb_mouse_t mouse_report;
  while(hal_usb_mouse_take(&mouse_report)==HAL_OK) {
    mouse.x=accumulate(mouse.x,mouse_report.x);
    mouse.y=accumulate(mouse.y,mouse_report.y);
    mouse.wheel=accumulate(mouse.wheel,mouse_report.wheel);
    mouse.time_ms=mouse_report.time_ms;
    mouse.buttons=mouse_report.buttons;mouse.pending=1;
  }
  /* 原始报告队列只有 8 项，必须每次都排空；漏掉这个循环会让
   * HAL 的 report_drops 一直涨（test usb 要求它为 0）。 */
  hal_usb_report_t report;
  while(hal_usb_report_take(&report)==HAL_OK)tests_usb_report(&report);
#endif
  if(!video||mode||!dirty||!hal_deadline_reached(hal_time_ms(),next_render))return;
  /* 每帧画满整屏。增量渲染在这里不成立：back 槽是隔一帧的历史，
   * 只补变化格会让其余格子显示陈旧像素（按键即花屏）。 */
  unsigned back=slot^1;volatile uint16_t *fb=hal_video_frame(back);
  for(unsigned r=0;r<ROWS;++r) {
    for(unsigned col=0;col<COLS;++col) {
      uint16_t fg,bg;
      vt_colors(&term.grid[r][col],&fg,&bg);
      cell(fb,col*6,r*8,term.grid[r][col].ch,1,fg,bg);
      drawn[r][col]=term.grid[r][col];
    }
    hal_poll();
  }
  paint_cursor(fb);
  if(hal_video_present(back)==HAL_OK) {slot=back;dirty=0;}
  next_render=hal_time_ms()+16;
}

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
