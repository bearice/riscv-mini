// VT100/ANSI 终端核心：状态机 + 属性网格 + 调色板 + 备用屏。
// 不依赖 HAL：渲染节流由调用方（bios_console_poll）驱动，便于主机测试直接编译。
#include "vt.h"

static vt_attr_t attr_default(void) {return (vt_attr_t)((ATTR_FG_DEFAULT << ATTR_FG_SHIFT) | (ATTR_BG_DEFAULT << ATTR_BG_SHIFT));}

void vt_reset(vt_t *t) {
  t->cx = t->cy = 0;
  t->top = 0; t->bottom = VT_ROWS - 1;
  t->attr = attr_default();
  t->saved_cx = t->saved_cy = 0; t->saved_attr = attr_default();
  t->main_cx = t->main_cy = t->main_wrap = 0; t->main_attr = attr_default();
  t->state = VT_GROUND; t->mode = 0; t->nparams = 0; t->sub = 0; t->charset = 0;
  t->wrap_pending = 0;
  t->report = 0; t->report_ctx = 0;
  for (unsigned i = 0; i < VT_COLS; ++i) t->tabs[i] = (i % 8 == 0) ? 1 : 0;
  for (unsigned i = 0; i < VT_MAXPARAM; ++i) t->params[i] = 0;
  for (unsigned r = 0; r < VT_ROWS; ++r)
    for (unsigned c = 0; c < VT_COLS; ++c) {
      t->main[r][c] = (vt_cell_t){' ', t->attr};
      t->alt[r][c] = (vt_cell_t){' ', t->attr};
    }
  t->grid = t->main;
}

static void clamp(vt_t *t) {
  if (t->cx >= VT_COLS) t->cx = VT_COLS - 1;
  if (t->cy >= VT_ROWS) t->cy = VT_ROWS - 1;
}

static void wrap(vt_t *t) {
  t->wrap_pending = 0;
  t->cx = 0;
  if (t->cy < t->bottom) ++t->cy; else vt_scroll_up(t);
}

static void newline(vt_t *t) {
  if (t->cy < t->bottom) ++t->cy; else vt_scroll_up(t);
}

void vt_scroll_up(vt_t *t) {
  for (unsigned r = t->top; r < t->bottom; ++r)
    for (unsigned c = 0; c < VT_COLS; ++c) t->grid[r][c] = t->grid[r + 1][c];
  for (unsigned c = 0; c < VT_COLS; ++c) t->grid[t->bottom][c] = (vt_cell_t){' ', t->attr};
}

void vt_scroll_down(vt_t *t) {
  for (unsigned r = t->bottom; r > t->top; --r)
    for (unsigned c = 0; c < VT_COLS; ++c) t->grid[r][c] = t->grid[r - 1][c];
  for (unsigned c = 0; c < VT_COLS; ++c) t->grid[t->top][c] = (vt_cell_t){' ', t->attr};
}

static void erase_region(vt_t *t, unsigned r0, unsigned c0, unsigned r1, unsigned c1) {
  for (unsigned r = r0; r <= r1; ++r)
    for (unsigned c = c0; c <= c1; ++c) t->grid[r][c] = (vt_cell_t){' ', t->attr};
}

static void erase_display(vt_t *t, unsigned mode) {
  if (mode == 0) {
    erase_region(t, t->cy, t->cx, t->cy, VT_COLS - 1);
    if (t->cy + 1 < VT_ROWS) erase_region(t, t->cy + 1, 0, VT_ROWS - 1, VT_COLS - 1);
    return;
  }
  if (mode == 1) {
    if (t->cy) erase_region(t, 0, 0, t->cy - 1, VT_COLS - 1);
    erase_region(t, t->cy, 0, t->cy, t->cx);
    return;
  }
  erase_region(t, 0, 0, VT_ROWS - 1, VT_COLS - 1);
}

static void erase_line(vt_t *t, unsigned mode) {
  if (mode == 0) { erase_region(t, t->cy, t->cx, t->cy, VT_COLS - 1); return; }
  if (mode == 1) { erase_region(t, t->cy, 0, t->cy, t->cx); return; }
  erase_region(t, t->cy, 0, t->cy, VT_COLS - 1);
}

// IL/DL 的作用范围是光标行到区域底边（含两端），底边上仍会插入/删除一行。
// IL 把光标行及以下的内容向下推，DL 把其下的内容向上提；被挤出区域的行丢弃。
static void insert_lines(vt_t *t, unsigned n) {
  if (t->cy < t->top || t->cy > t->bottom) return;
  if (n > t->bottom - t->cy + 1) n = t->bottom - t->cy + 1;
  for (unsigned r = t->bottom; r >= t->cy + n; --r)
    for (unsigned c = 0; c < VT_COLS; ++c) t->grid[r][c] = t->grid[r - n][c];
  for (unsigned r = t->cy; r < t->cy + n; ++r)
    for (unsigned c = 0; c < VT_COLS; ++c) t->grid[r][c] = (vt_cell_t){' ', t->attr};
}

static void delete_lines(vt_t *t, unsigned n) {
  if (t->cy < t->top || t->cy > t->bottom) return;
  if (n > t->bottom - t->cy + 1) n = t->bottom - t->cy + 1;
  for (unsigned r = t->cy; r + n <= t->bottom; ++r)
    for (unsigned c = 0; c < VT_COLS; ++c) t->grid[r][c] = t->grid[r + n][c];
  for (unsigned r = t->bottom - n + 1; r <= t->bottom; ++r)
    for (unsigned c = 0; c < VT_COLS; ++c) t->grid[r][c] = (vt_cell_t){' ', t->attr};
}

static void insert_chars(vt_t *t, unsigned n) {
  if (n > VT_COLS - t->cx) n = VT_COLS - t->cx;
  for (unsigned c = VT_COLS; c-- > t->cx + n;) t->grid[t->cy][c] = t->grid[t->cy][c - n];
  for (unsigned c = t->cx; c < t->cx + n; ++c) t->grid[t->cy][c] = (vt_cell_t){' ', t->attr};
}

static void delete_chars(vt_t *t, unsigned n) {
  if (n > VT_COLS - t->cx) n = VT_COLS - t->cx;
  for (unsigned c = t->cx; c + n < VT_COLS; ++c) t->grid[t->cy][c] = t->grid[t->cy][c + n];
  for (unsigned c = VT_COLS - n; c < VT_COLS; ++c) t->grid[t->cy][c] = (vt_cell_t){' ', t->attr};
}

// 私有前缀存在 t->sub（? < > !），模式切换由 final 决定，二者不能混用。
static void set_mode(vt_t *t, unsigned final) {
  unsigned m = t->mode;
  if (final == 'r') {  // DECSTBM：设置滚动区域并把光标移到区域左上
    unsigned top = t->params[0] ? t->params[0] - 1 : 0;
    unsigned bottom = t->params[1] ? t->params[1] - 1 : VT_ROWS - 1;
    if (bottom >= VT_ROWS) bottom = VT_ROWS - 1;
    if (top >= bottom) return;
    t->top = top; t->bottom = bottom;
    t->cx = 0; t->cy = top;
    return;
  }
  if (final == 'h' || final == 'l') {  // DECSET / DECRST
    unsigned bit = 0;
    switch (t->params[0]) {
      case 1: bit = VT_MODE_APPKEYS; break;
      case 25: bit = VT_MODE_CURSOR; break;
      case 47: case 1047: case 1049: bit = VT_MODE_ALT; break;
      default: return;
    }
    if (final == 'h') m |= bit; else m &= ~bit;
    if (bit == VT_MODE_ALT) {
      if ((m & VT_MODE_ALT) && !(t->mode & VT_MODE_ALT)) {
        if (t->params[0] == 1049) {
          t->main_cx = t->cx; t->main_cy = t->cy;
          t->main_attr = t->attr; t->main_wrap = t->wrap_pending;
        }
        t->grid = t->alt;
        erase_region(t, 0, 0, VT_ROWS - 1, VT_COLS - 1);
        t->cx = 0; t->cy = 0;
        t->wrap_pending = 0;
      } else if (!(m & VT_MODE_ALT) && (t->mode & VT_MODE_ALT)) {
        t->grid = t->main;
        if (t->params[0] == 1049) {
          t->cx = t->main_cx; t->cy = t->main_cy;
          t->attr = t->main_attr; t->wrap_pending = t->main_wrap;
        }
      }
    }
    t->mode = m;
  }
}

static void sgr(vt_t *t) {
  if (!t->nparams) { t->params[0] = 0; t->nparams = 1; }
  for (unsigned i = 0; i < t->nparams; ++i) {
    unsigned p = t->params[i];
    /* 逐参数增量修改，互不牵连：每个参数只动自己负责的位。
     * 早先的写法在每条 SGR 开头统一清掉 bold/reverse/fg/bg，会把
     * ESC[1;31m 这种多参数序列里前面刚设的位又清掉（只剩最后一个参数生效）。 */
    if (p == 0) { t->attr = attr_default(); continue; }
    if (p == 1) { t->attr |= ATTR_BOLD; continue; }
    if (p == 2) { t->attr &= ~ATTR_BOLD; continue; }
    if (p == 7) { t->attr |= ATTR_REVERSE; continue; }
    if (p == 22) { t->attr &= ~ATTR_BOLD; continue; }
    if (p == 27) { t->attr &= ~ATTR_REVERSE; continue; }
    if (p >= 30 && p <= 37) { t->attr = (vt_attr_t)((t->attr & ~ATTR_FG_MASK) | ((p - 30) << ATTR_FG_SHIFT)); continue; }
    if (p == 39) { t->attr = (vt_attr_t)((t->attr & ~ATTR_FG_MASK) | (ATTR_FG_DEFAULT << ATTR_FG_SHIFT)); continue; }
    if (p >= 40 && p <= 47) { t->attr = (vt_attr_t)((t->attr & ~ATTR_BG_MASK) | ((p - 40) << ATTR_BG_SHIFT)); continue; }
    if (p == 49) { t->attr = (vt_attr_t)((t->attr & ~ATTR_BG_MASK) | (ATTR_BG_DEFAULT << ATTR_BG_SHIFT)); continue; }
    if (p >= 90 && p <= 97) { t->attr = (vt_attr_t)((t->attr & ~ATTR_FG_MASK) | ((p - 90 + 8) << ATTR_FG_SHIFT)); continue; }
    if (p >= 100 && p <= 107) { t->attr = (vt_attr_t)((t->attr & ~ATTR_BG_MASK) | ((p - 100 + 8) << ATTR_BG_SHIFT)); continue; }
  }
}

static unsigned def(vt_t *t, unsigned i, unsigned d) {
  return (i < t->nparams && t->params[i]) ? t->params[i] : d;
}

static void vt_report(vt_t *t, const char *text) {
  if (t->report) t->report(t->report_ctx, text);
}

static void csi(vt_t *t, unsigned final) {
  // 只在光标移动或编辑时取消待换行；SGR、报告和模式查询不移动光标。
  switch (final) {
    case 'A': case 'B': case 'C': case 'D': case 'E': case 'F':
    case 'G': case '`': case 'H': case 'f': case 'J': case 'K':
    case 'L': case 'M': case '@': case 'P': case 'd': case 'r':
    case 'X': case 'Z': case 'u': t->wrap_pending = 0; break;
    default: break;
  }
  switch (final) {
    case 'A': { unsigned n = def(t, 0, 1); t->cy = t->cy >= n ? t->cy - n : 0; if (t->cy < t->top) t->cy = t->top; break; }
    case 'B': { unsigned n = def(t, 0, 1); t->cy += n; if (t->cy > t->bottom) t->cy = t->bottom; break; }
    case 'C': { unsigned n = def(t, 0, 1); t->cx += n; if (t->cx >= VT_COLS) t->cx = VT_COLS - 1; break; }
    case 'D': { unsigned n = def(t, 0, 1); t->cx = t->cx >= n ? t->cx - n : 0; break; }
    case 'E': { unsigned n = def(t, 0, 1); t->cx = 0; t->cy += n; if (t->cy > t->bottom) t->cy = t->bottom; break; }
    case 'F': { unsigned n = def(t, 0, 1); t->cx = 0; t->cy = t->cy >= n ? t->cy - n : 0; if (t->cy < t->top) t->cy = t->top; break; }
    case 'G': case '`': t->cx = def(t, 0, 1) - 1; clamp(t); break;
    case 'H': case 'f': {
      unsigned row = def(t, 0, 1) - 1, col = def(t, 1, 1) - 1;
      if (row >= VT_ROWS) row = VT_ROWS - 1;
      if (col >= VT_COLS) col = VT_COLS - 1;
      t->cy = row; t->cx = col;
      break;
    }
    case 'J': erase_display(t, def(t, 0, 0)); break;
    case 'K': erase_line(t, def(t, 0, 0)); break;
    case 'L': insert_lines(t, def(t, 0, 1)); break;
    case 'M': delete_lines(t, def(t, 0, 1)); break;
    case '@': insert_chars(t, def(t, 0, 1)); break;
    case 'P': delete_chars(t, def(t, 0, 1)); break;
    case 'd': { unsigned n = def(t, 0, 1); t->cy = n - 1; if (t->cy >= VT_ROWS) t->cy = VT_ROWS - 1; break; }
    case 'm': sgr(t); break;
    case 'r': set_mode(t, 'r'); break;   // DECSTBM
    case 'h': case 'l': set_mode(t, final); break;  // DECSET/RESET（?1/?25/?47/?1047/?1049）
    case 'X': { unsigned n = def(t, 0, 1); if (n > VT_COLS - t->cx) n = VT_COLS - t->cx; erase_region(t, t->cy, t->cx, t->cy, t->cx + n - 1); break; }
    case 'Z': {  // CBT：反向制表
      unsigned n = def(t, 0, 1);
      while (n--) {
        if (!t->cx) { if (t->cy > t->top) { --t->cy; t->cx = VT_COLS - 1; } else break; }
        t->cx = (t->cx - 1) / 8 * 8;
      }
      break;
    }
    case 'g': {  // DECTCLEAR：清/设默认制表位
      unsigned n = def(t, 0, 0);
      if (n == 0 || n == 3) for (unsigned i = 0; i < VT_COLS; ++i) t->tabs[i] = 0;
      else if (n == 2) for (unsigned i = 0; i < VT_COLS; ++i) t->tabs[i] = (i % 8 == 7) ? 1 : 0;
      break;
    }
    case 'c': if (t->sub == '>') { vt_report(t, "\x1b[?1;2c"); } else { vt_report(t, "\x1b[?6c"); } break;
    case 'v': {  // RTCURS：恢复保存的状态
      t->cx = t->saved_cx; t->cy = t->saved_cy; t->attr = t->saved_attr; clamp(t);
      break;
    }
    case 'w': {  // SCURS：保存当前状态
      t->saved_cx = t->cx; t->saved_cy = t->cy; t->saved_attr = t->attr;
      break;
    }
    case 'n': {  // DSR：设备状态报告（经 report 回调回显）
      unsigned n = def(t, 0, 0);
      if (n == 5) vt_report(t, "\x1b[0n");
      else if (n == 6) {
        char b[24], *p = b;
        *p++ = 0x1b; *p++ = '[';
        unsigned row = t->cy + 1, col = t->cx + 1;
        char digits[8]; int k = 0;
        do { digits[k++] = (char)('0' + row % 10); row /= 10; } while (row);
        while (k) *p++ = digits[--k];
        *p++ = ';';
        k = 0;
        do { digits[k++] = (char)('0' + col % 10); col /= 10; } while (col);
        while (k) *p++ = digits[--k];
        *p++ = 'R'; *p = 0;
        vt_report(t, b);
      }
      break;
    }
    case 'p': {  // DECXTR：软复位，保留模式与备用屏选择
      vt_attr_t attr = t->attr;
      unsigned mode = t->mode, top = t->top, bottom = t->bottom;
      vt_cell_t (*grid)[VT_COLS] = t->grid;
      vt_reset(t);
      t->attr = attr; t->mode = mode; t->top = top; t->bottom = bottom; t->grid = grid;
      break;
    }
    case 'q': {  // DECSCUSR：光标样式（0-2=隐藏，其余=显示）
      unsigned style = def(t, 0, 1);
      if (style <= 2) t->mode &= ~VT_MODE_CURSOR; else t->mode |= VT_MODE_CURSOR;
      break;
    }
    case 's': t->saved_cx = t->cx; t->saved_cy = t->cy; t->saved_attr = t->attr; break;  // xterm save cursor
    case 'u': t->cx = t->saved_cx; t->cy = t->saved_cy; t->attr = t->saved_attr; clamp(t); break;  // xterm restore cursor
    default: break;  // 未知 CSI：吞掉
  }
  t->state = VT_GROUND;
}

static void esc(vt_t *t, unsigned ch) {
  if (ch == '8' || ch == 'D' || ch == 'E' || ch == 'M') t->wrap_pending = 0;
  switch (ch) {
    case '[': t->state = VT_CSI; t->nparams = 0; t->sub = 0; for (unsigned i = 0; i < VT_MAXPARAM; ++i) t->params[i] = 0; return;
    case '7': t->saved_cx = t->cx; t->saved_cy = t->cy; t->saved_attr = t->attr; t->state = VT_GROUND; return;
    case '8': t->cx = t->saved_cx; t->cy = t->saved_cy; t->attr = t->saved_attr; clamp(t); t->state = VT_GROUND; return;
    case 'c': vt_reset(t); t->state = VT_GROUND; return;
    case 'D': newline(t); t->state = VT_GROUND; return;
    case 'E': t->cx = 0; newline(t); t->state = VT_GROUND; return;
    case 'H': t->tabs[t->cx] = 1; t->state = VT_GROUND; return;  // HTS
    case 'M': if (t->cy > t->top) { --t->cy; } else { vt_scroll_down(t); } t->state = VT_GROUND; return;  // RI 反向换行
    case '(': case ')': case '*': case '+': case '#': case '%': t->charset = 1; t->state = VT_GROUND; return;  // 字符集/特殊前缀：吞掉下一字节
    default: t->state = VT_GROUND; return;  // 其余 ESC 序列吞掉
  }
}

void vt_putc(vt_t *t, unsigned char ch) {
  switch (t->state) {
    case VT_ESC:
      esc(t, ch);
      return;
    case VT_CSI:
      if (ch >= '0' && ch <= '9') {
        unsigned *p = &t->params[t->nparams ? t->nparams - 1 : 0];
        if (*p < 10000u) *p = *p * 10u + (ch - '0');
        if (!t->nparams) t->nparams = 1;
        return;
      }
      if (ch == ';') { if (t->nparams < VT_MAXPARAM) ++t->nparams; return; }
      if (ch == '<' || ch == '>' || ch == '?' || ch == '!') { t->sub = ch; return; }
      if (ch >= ' ' && ch <= '?') return;  // 私有参数与中间字节
      if (ch >= 0x40 && ch <= 0x7e) csi(t, ch);
      else t->state = VT_GROUND;
      return;
    default: break;
  }
  if (ch == 0x1b) { t->state = VT_ESC; t->charset = 0; return; }
  if (t->charset) { t->charset = 0; return; }  // 字符集选择字节（B/0 等）：吞掉
  switch (ch) {
    case '\r': t->cx = 0; t->wrap_pending = 0; return;
    case '\n': t->wrap_pending = 0; newline(t); return;
    case 8: t->wrap_pending = 0; if (t->cx) --t->cx; return;
    case 9: {  // HT：前进到下一个制表位（严格大于当前列）
      t->wrap_pending = 0;
      unsigned next = t->cx + 1;
      while (next < VT_COLS && !t->tabs[next]) ++next;
      t->cx = next >= VT_COLS ? VT_COLS - 1 : next;
      return;
    }
    case 7: t->wrap_pending = 0; return;  // BEL：无声
    default: break;
  }
  if (ch < 32 || ch == 127) return;
  if (t->wrap_pending) wrap(t);   // 延迟换行：先换行再落字，避免刚好 80 列时多滚一行
  t->grid[t->cy][t->cx] = (vt_cell_t){(char)ch, t->attr};
  if (++t->cx >= VT_COLS) { t->cx = VT_COLS - 1; t->wrap_pending = 1; }
}

uint16_t vt_rgb(unsigned color) {
  /* 索引 0-15 = ANSI 黑红绿黄蓝品青灰 / 亮黑红绿黄蓝品白。
   * 调色板顺序与 firmware/diagnostics/tests.c 里已上板确认的文字色一致
   * （tests.c 用 0xffff 作前景、0x0841 作背景），不要凭 RGB565 位域推导重排。 */
  static const uint16_t palette[16] = {
    0x0000, 0xf800, 0x07e0, 0xffe0, 0x001f, 0xf81f, 0x07ff, 0xbdf7,
    0x528a, 0xf851, 0x0fe0, 0xfff1, 0x48ff, 0xf9ff, 0x0fff, 0xffff,
  };
  return palette[color & 15u];
}

void vt_colors(const vt_cell_t *cell, uint16_t *fg, uint16_t *bg) {
  unsigned f = (cell->attr >> ATTR_FG_SHIFT) & 15u, b = (cell->attr >> ATTR_BG_SHIFT) & 15u;
  if (cell->attr & ATTR_REVERSE) { unsigned s = f; f = b; b = s; }
  if ((cell->attr & ATTR_BOLD) && f >= 1 && f <= 7) f += 8;
  *fg = vt_rgb(f);
  *bg = vt_rgb(b);
}
