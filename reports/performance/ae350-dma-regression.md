# AE350 DMA 平台内存性能回归

2026-10-20。ILM 代码、DLM 栈/结果，64 KiB，每项三轮。此处只报 cycles，不借用旧 counter 标定换算新候选带宽。copy 计时与写操作包含末尾写回。不是 DMAC 吞吐测量。

| cache | 操作 | cycles 中位数 |
|---|---|---:|
| uncached | write | 1835103 |
| uncached | read | 7378761 |
| uncached | copy | 9961315 |
| write-back-cold | write | 1973777 |
| write-back-cold | read | 1470527 |
| write-back-cold | copy | 3491233 |

完整原始样本与身份见 [JSON](ae350-dma-regression.json)。回执：`build/runs/20261020T081410037685Z-board-ae350-ddr-suite/result.json`。带宽测试完整性错误为 0，DDR 功能 13 checks / 0 failures。仅进行 1 MiB 遍历和短压力，不将它重标为全容量或长期稳定性验收。
