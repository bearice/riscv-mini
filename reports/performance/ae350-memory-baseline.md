# AE350 内存性能 baseline

2026-10-19。当前交付点的可复用基准；保留三次原始数据。此文件不更新 20K 全局 baseline。

当前 gateware：`92c86afd1768d27351e5819a4c7eb8a9d9c068ee660fe3e0f50a57fb61badc60`；对照：`6a2e1381d170b6b33f61aa094106c6f29c5714e93063b7c62bdfd46a6cbc1fac`；相同诊断载荷：`8e204917c296b9a036cda43ebba9505ad17b91b4ede358dfbc34ccc36fc5ea6d`。

数据 AHB 100 MHz，外设/ITCM 80 MHz，DDR UI 100 MHz，CK 400 MHz，core 目标 800 MHz。QPC 与 mcycle 三次采样得到 counter 速率约 800.521 MHz；吞吐为该速率的估算换算，不是物理时钟或器件上限测量。

带宽代码在 ILM、栈/结果在 DLM，32-bit volatile 访问，每次 64 KiB。D-cache 分别关闭或 cold write-back；每次先清理/失效，写和 copy 计时包含末尾 clean/fence。copy 的逻辑字节数是复制长度，实际读写流量另列。

| 模式 | 操作 | 当前 cycles 中位数 | 对照 cycles | 当前估算 MiB/s | 吞吐比 当前/对照 | UI 读/写命令 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| uncached | write | 1835103 | 1572959 | 27.26 | 0.857x | 0 / 16384 |
| uncached | read | 7378769 | 7378749 | 6.78 | 1.000x | 16384 / 0 |
| uncached | copy | 9961311 | 9684991 | 5.02 | 0.972x | 16384 / 16384 |
| write-back-cold | write | 1973777 | 2282603 | 25.35 | 1.156x | 2048 / 2048 |
| write-back-cold | read | 1470535 | 1470815 | 34.02 | 1.000x | 2048 / 0 |
| write-back-cold | copy | 3491233 | 3804767 | 14.33 | 1.090x | 4096 / 2048 |

一次 32 B cache write-back：对照 4 条 UI 写，当前 1 条；64 KiB cached 写的 UI 写命令从 8192 降至 2048。cached write/copy 的端到端提升小于命令数改善幅度；独立 uncached write 的 cycles 增加约 16.7%，保留为后续优化的真实基线。独立 uncached 小访问仍是一条 32 B UI 命令服务 4 B，不能宣称所有访问已消除位宽浪费。按工作量与已验证 mask 合同计算，当前 cached 写的有效写字节/UI 写容量为 100%，独立 uncached 写为 12.5%；这不是额外硬件 byte-mask 累加器的实测值。

依赖加载三次 cycles：`[1780840, 1780560, 1780512]`；1024 hops，中位数 1780560 cycles，即 1738.83 cycles/hop，约 2172.1 ns/hop。指针链占 16 KiB；代码在 DDR、I/D cache 关闭，因此含取指争用，不能作为纯 DRAM 延迟。

短压力请求为配置频率的 5 秒 cycle budget；实际一轮完整工作量运行 6.563 秒（墙钟 6.594 秒、暂停 0.031 秒），1 MiB 工作集，0 错。64 位计时防止单轮跨越低 32 位回绕而漏算。

原始数据与来源：

- [结构化 baseline](ae350-memory-baseline.json)：全部三次样本、counter 测量和来源哈希。
- [当前测量](../../build/runs/20261019T135924612116Z-board-ae350-ddr-suite/result.json)；[同载荷对照](../../build/runs/20261019T140142019606Z-board-ae350-ddr-suite/result.json)；[QPC/counter 标定](../../build/runs/20261019T140431107382Z-board-ae350-jtag-test/result.json)。

复测使用 `mini.py board ddr-suite --build <准确gateware回执> --payload <准确诊断回执> --flags 5 --stress-seconds 5`，显式指定板卡串口与两个 JTAG location。保留独立 run，不覆盖本 baseline；完整性复验另加 `--flags 15 --traversal-mb 1024`。
