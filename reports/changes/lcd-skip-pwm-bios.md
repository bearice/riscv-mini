# BIOS compact LCD and PWM diagnostics

生成：2026-10-14T07:38:34+00:00；模式：staged review。

## 改动

- Add test lcd skip to exercise RGB565/INDEX8 compact frames, continued DMA while dimming, and measured PWM at 0/25/50/75/100 percent; include the test in firmware verification for compatible CSRs.
- Versioned single-microphone candidate passes board firmware diagnostics and 60-second soak, with LCD underflows and audio errors remaining zero.

## 验证

对应源码的检查：18 项，PASS；命令和输入身份见同名 JSON。

性能依据：`reports/performance/lcd-skip-pwm-mono.md`。

完整改动以 Git 记录为准；本记录只保留改动说明和验证依据。
