# Linux root-port debounce and RGB-only framebuffer

生成：2026-10-10T13:56:44+00:00；模式：staged review。

## 改动

- Debounce cached USB root-port connection state; packet K, short EOP, reset and transfer must not produce false disconnects. Actual callback regression fails on old code and passes on new code.
- Remove SPI panel framebuffer and fb1 selftest per user scope; keep RGB LCD fb0, SPI controller and GPIO. Kernel build checks forbidden panel configuration and symbol.
- Compiled candidate BusyBox board receipt 20261010T135242110501Z-linux passes LCD tty1 shell, PTY, USB keyboard registration and userspace checks, with only /dev/fb0 and no logged root disconnect or Cannot enable. Linux/board host regression and 18 workflow tests pass. Earlier native ALSA underrun failure is retained; audio and long-term physical hotplug are not requalified by this fix.

## 验证

对应源码的检查：18 项，PASS；命令和输入身份见同名 JSON。

完整改动以 Git 记录为准；本记录只保留改动说明和验证依据。
