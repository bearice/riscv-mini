# Default to qualified fast arithmetic and INDEX8 Doom with static Newlib apps

生成：2026-10-13T05:53:59+00:00；模式：staged review。

## 改动

- Add a reusable static Newlib syscall runtime for BIOS apps and replace the Doom libc shim; BIOS remains freestanding.
- Add INDEX8 video with two hardware palettes, atomic frame/palette switching and viewport clipping while preserving RGB565 text mode.
- Qualify fast CPU arithmetic with an 8 KiB shared L2, select the accepted configuration through current, and make INDEX8/soft float the Doom CLI defaults.
- Preserve matched performance comparisons and failed experiments; current-source PnR 0/0, firmware/60-second soak, all 66 benchmark rows and Doom CRC checks pass.

## 验证

对应源码的检查：18 项，PASS；命令和输入身份见同名 JSON。

性能依据：`reports/performance/doom-default-performance.md`。

完整改动以 Git 记录为准；本记录只保留改动说明和验证依据。
