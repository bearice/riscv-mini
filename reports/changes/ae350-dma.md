# AE350 reusable DMA platform and acceptance

生成：2026-10-20T08:16:21+00:00；模式：staged review。

## 改动

- Expose platform CSR, loopback AHB and hard DMAC handshake; provide shared polling DMA submission and ranged cache maintenance with explicit limitations; replace bring-up probes with dma-suite and retain measured acceptance scope.

## 验证

对应源码的检查：20 项，PASS；命令和输入身份见同名 JSON。

性能依据：`reports/performance/ae350-dma-regression.md`。

完整改动以 Git 记录为准；本记录只保留改动说明和验证依据。
