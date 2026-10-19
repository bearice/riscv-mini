# Finalize reusable AE350 memory platform and baseline

生成：2026-10-19T14:26:39+00:00；模式：staged review。

## 改动

- Retire the fabric ROM/SRAM monitor and phase-specific integration paths; retain ITCM boot, hard local memory and the qualified DDR platform as the current implementation.
- Preserve reproducible full-window, loader/recovery, CDC and three-sample performance evidence, including uncached regressions and supported reset limits.
- Validate content-addressed board receipts in commit checks and expose dry-run cleanup for rebuildable work caches without deleting stores, evidence or final archives.

## 验证

对应源码的检查：19 项，PASS；命令和输入身份见同名 JSON。

性能依据：`reports/performance/ae350-memory-baseline.md`。

完整改动以 Git 记录为准；本记录只保留改动说明和验证依据。
