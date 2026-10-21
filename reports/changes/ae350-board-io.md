# AE350 reusable board I/O and interrupt services

生成：2026-10-21T03:13:40+00:00；模式：staged review。

## 改动

- Share debounce and WS2812 cores across 20K and AE350 adapters, generate board bindings, provide GPIO/PLIC services and a reusable digital I/O suite. Qualify the current 80/100/400 MHz candidate with DDR/DMA overlap and performance evidence. Keep fan control outside acceptance and remove fan writes from the demonstration; retain physical observation and temperature accuracy boundaries.

## 验证

对应源码的检查：20 项，PASS；命令和输入身份见同名 JSON。

性能依据：`reports/performance/ae350-board-io.md`。

完整改动以 Git 记录为准；本记录只保留改动说明和验证依据。
