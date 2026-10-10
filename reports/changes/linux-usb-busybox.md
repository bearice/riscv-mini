# Linux RV32 port, BusyBox LCD shell and USB keyboard recovery

生成：2026-10-10T13:30:34+00:00；模式：staged review。

## 改动

- Add Linux 6.12.112 RV32IMA/Sv32 with native IRQ/UART, coherent framebuffer, MMC, Ethernet ring DMA, USB full-speed host, ALSA, GPIO/input/LED, SPI panel and read-only Flash drivers; preserve documented scope and driver limitations.
- Provide pinned static BusyBox/musl initrd with serial and LCD tty1 ash, UNIX98 PTY, and checked SRAM/UART/TFTP boot workflow; reject incompatible images and host binaries.
- Fix USB receive clock recovery at jittered SYNC/PID edges; release TinyUSB enumeration on expected EP0-open failure instead of trapping. PHY 48/60 MHz phase/jitter regression and real TinyUSB/HCD rejection regression pass.
- Host 52 focused plus 18 workflow tests pass; captured candidate PnR 0/0, matching BIOS full suite/60-second soak and three all/cache rounds pass. Linux native receipt 20261010T080621373234Z-linux and BusyBox/LCD/PTY receipt 20261010T132255233230Z-linux pass; earlier ALSA capture failure remains documented. No Flash or SD writes.

## 验证

对应源码的检查：18 项，PASS；命令和输入身份见同名 JSON。

性能依据：`reports/performance/linux-usb-vs-v0.9.1.md`。

完整改动以 Git 记录为准；本记录只保留改动说明和验证依据。
