# Video driver supports compact scanout buffers

生成：2026-10-14T07:38:33+00:00；模式：staged review。

## 改动

- Validate buffer sizes against compact window geometry when skip is enabled; clear skip when restoring standard graphics, format or window configuration.

## 验证

对应源码的检查：18 项，PASS；命令和输入身份见同名 JSON。

性能依据：`reports/performance/lcd-skip-pwm-mono.md`。

完整改动以 Git 记录为准；本记录只保留改动说明和验证依据。
