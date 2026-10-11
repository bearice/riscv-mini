# LCD DMA 环形扫描与 Linux YWRAP 滚屏

生成：2026-10-11T03:42:49+00:00；模式：staged review。

## 改动

- 增加帧边界锁存的 wrap_start/wrap_end，校验 DDR 和 16 字节对齐边界；默认关闭且旧 CSR 布局保持兼容。
- Linux fb0 在单帧缓冲上启用 YWRAP，并在图形自检前复位显示起点；DT 可选属性兼容旧硬件。
- 实板固件、Linux fb0、BusyBox/PTY/HID 枚举及像素回读通过；120 行 LCD 滚屏从 21.53 s 降至 8.96/8.79 s，5 s 阈值仍失败，PTY 本身未优化。

## 验证

对应源码的检查：18 项，PASS；命令和输入身份见同名 JSON。

性能依据：`reports/performance/lcd-ring-bios.md`。

完整改动以 Git 记录为准；本记录只保留改动说明和验证依据。
