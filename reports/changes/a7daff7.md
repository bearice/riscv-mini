# U-Boot RGB LCD framebuffer console

生成：2026-10-08T03:36:26+00:00；模式：staged review。

## 改动

- Add a DM video driver for the 480x272 RGB565 LCD using the programmable base0/base1 DMA interface.
- Generate the matching LCD CSR device-tree node from the selected SoC ABI and mirror U-Boot output to serial and vidconsole.
- Add host and board verification for framebuffer pixels, DMA progress, address errors, and underflows.

## 验证

对应源码的检查：18 项，PASS；命令和输入身份见同名 JSON。

完整改动以 Git 记录为准；本记录只保留改动说明和验证依据。
