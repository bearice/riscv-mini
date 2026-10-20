# AE350 mem-domain burst read-line return

生成：2026-10-20T10:10:05+00:00；模式：staged review。

## 改动

- Return the full DDR block once across CDC, serve continuing read-burst beats in mem, and remove UI-side reuse state. Preserve NONSEQ isolation, write aggregation and registered interconnect; expose coherent diagnostic counters. Qualify the selected operating point with SRAM board receipts and nine samples per performance row; retain the original baseline and document 10 ps setup margin.

## 验证

对应源码的检查：20 项，PASS；命令和输入身份见同名 JSON。

性能依据：`reports/performance/ae350-read-line.md`。

完整改动以 Git 记录为准；本记录只保留改动说明和验证依据。
