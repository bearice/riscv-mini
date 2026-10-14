# CSR-controlled compact LCD scanout and PWM backlight

生成：2026-10-14T07:38:32+00:00；模式：staged review。

## 改动

- Add skip-controlled compact RGB565/INDEX8 scanout with direct black margins and bounded DMA geometry; preserve full-screen and ring operation when skip is disabled.
- Drive E10 backlight with 45 kHz PWM, 0..100 percent duty and synchronized output observation; reset remains full brightness.
- Qualification uses one microphone, RV32IMA 60/120 MHz and 8 KiB L2: PnR setup/hold 0/0, firmware/LCD/PWM/mic/60-second soak pass. Worst setup slack +0.031 ns; physical brightness remains unobserved.

## 验证

对应源码的检查：18 项，PASS；命令和输入身份见同名 JSON。

性能依据：`reports/performance/lcd-skip-pwm-mono.md`。

完整改动以 Git 记录为准；本记录只保留改动说明和验证依据。
