# Tang Mega 138K Pro / AE350

机器 `mega138k_ae350`，板卡 `mega138k_pro`。AE350 硬核从厂商 ITCM 启动，使用硬核 ILM/DLM 与零基址 DDR；普通应用和诊断载荷经统一 UART loader 装载。

当前构建参数来自 [Machine JSON](../../../gateware/machines/mega138k_ae350/machine.json) 与 [Board JSON](../../../gateware/boards/mega138k_pro/board.json)。20K 的 BIOS、L2 启动 RAM、XIP 分区与 current 不用于本机器；共享构建入口不表示已支持相同固件和发布套件。

| 主题 | 文档 |
| --- | --- |
| 构建 | [目标与增量 PnR](build.md)、[硬核资源说明](gw5ast-hard-ip-usage.md) |
| 启动与操作 | [平台启动、装载与专项命令](operations.md)、[bootloader](bootloader.md)、[CPU JTAG](jtag.md) |
| 内存与接口 | [地址合同](address-map.md)、[cache](cache.md)、[DMA](dma.md)、[内存验证合同](memory-validation.md) |
| 板级设备 | [I/O 与中断](io.md)、[音频与麦克风](audio.md)、[HDMI](video.md) |
| 能力范围 | [已实现与未交付能力](capabilities.md) |
| 验收记录 | [内存验收](../../../reports/ae350-p2.md)、[DMA](../../../reports/ae350-dma.md)、[音频](../../../reports/ae350-audio.md)、[视频](../../../reports/ae350-video.md) |
| 性能 | [内存 baseline](../../../reports/performance/ae350-memory-baseline.md)、[板级 I/O](../../../reports/performance/ae350-board-io.md)、[读行优化](../../../reports/performance/ae350-read-line.md) |

共享规则见 [文档目录](../../README.md) 和 [组件构建](../../common/component-builds.md)。重复构建复用同一 workspace；板测使用准确产物身份与 SRAM/UART，执行交接和套件通过分别记录。
