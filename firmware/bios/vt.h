// BIOS TTY 的 VT100/ANSI 终端核心（纯状态机 + 属性网格，不依赖 HAL，可在主机直接编译）。
#ifndef BIOS_VT_H
#define BIOS_VT_H
#include <stdint.h>

#define VT_COLS 80
#define VT_ROWS 34
#define VT_MAXPARAM 16

// 属性位（16 位）：bit15 reverse，bit14 bold，bits13-10 前景 0-15，bits9-6 背景 0-15。
#define ATTR_REVERSE 0x8000u
#define ATTR_BOLD 0x4000u
#define ATTR_FG_SHIFT 10u
#define ATTR_FG_MASK 0x3c00u
#define ATTR_BG_SHIFT 6u
#define ATTR_BG_MASK 0x3c0u
/* 默认前景索引。调色板是 ANSI 顺序：1=红、7=浅灰、15=白。
 * 旧 BIOS 的文字是纯白 0xffff，所以默认取 15，不要用 1。 */
#define ATTR_FG_DEFAULT 15u
#define ATTR_BG_DEFAULT 0u

typedef uint16_t vt_attr_t;

typedef struct { char ch; vt_attr_t attr; } vt_cell_t;
typedef vt_cell_t vt_grid_t[VT_ROWS][VT_COLS];

enum { VT_GROUND = 0, VT_ESC, VT_CSI };
enum { VT_MODE_APPKEYS = 1u, VT_MODE_CURSOR = 2u, VT_MODE_ALT = 4u };

typedef struct {
  vt_cell_t (*grid)[VT_COLS];  // 当前活动网格（main 或 alt）
  vt_grid_t main, alt;
  unsigned cx, cy;      // 光标（0 基）
  unsigned top, bottom; // 滚动区域（DECSTBM，0 基，含端点）
  vt_attr_t attr, saved_attr;
  unsigned saved_cx, saved_cy;
  unsigned wrap_pending; // 光标已停在最后一列，下一个可打印字符才真正换行（DECAWM 延迟换行）
  unsigned state;
  unsigned params[VT_MAXPARAM], nparams;
  unsigned sub;         // CSI 私有前缀（? < > !）
  unsigned charset;     // ESC ( ) * + 转义态：吞掉字符集选择字节
  unsigned mode;        // VT_MODE_* 位
  uint8_t tabs[VT_COLS];  // 制表位（1=有）
  void (*report)(void *ctx, const char *text);  // DSR 等回显（可选，NULL=不回显）
  void *report_ctx;
} vt_t;

void vt_reset(vt_t *t);
void vt_putc(vt_t *t, unsigned char ch);
void vt_scroll_up(vt_t *t);
void vt_scroll_down(vt_t *t);
uint16_t vt_rgb(unsigned color);                 // 0-15 → RGB565
void vt_colors(const vt_cell_t *cell, uint16_t *fg, uint16_t *bg);

#endif
