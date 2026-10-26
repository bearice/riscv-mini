# Tang Mega 138K Pro / AE350

机器 `mega138k_ae350`，板卡 `mega138k_pro`。当前源码使用配置 Flash U4 官方 0x600000 固件分区 8 KiB XIP bootrom，不实例化 fabric ITCM。用户已决定 sys/AHB/APB 使用 50 MHz，CPU 800 MHz、mem/DDR UI 100 MHz、DDR CK 400 MHz；音频回到 sys 域，移除此前提频增加的独立音频 CDC、额外 AHB 响应中继和 APB 多级读回流水。最小Flash与完整系统均在50MHz通过STA，bootrom/配置Flash读回及重载启动已验收，见[50 MHz 简化记录](../../../reports/ae350-sys50-simplification.md)。使用硬核 ILM/DLM 与零基址 DDR；普通应用和诊断载荷经统一 UART loader 装载。安装方法见 [bootloader](bootloader.md)，隔离结果见[最小系统探测](../../../reports/ae350-minimal-flash-timing.md)；既有 80 MHz 板测报告不代表新组合已上板通过。

当前构建参数来自 [Machine JSON](../../../gateware/machines/mega138k_ae350/machine.json) 与 [Board JSON](../../../gateware/boards/mega138k_pro/board.json)。20K 的 BIOS、L2 启动 RAM、XIP 分区与 current 不用于本机器；共享构建入口不表示已支持相同固件和发布套件。

控制寄存器与低带宽外设使用 50 MHz sys。大量数据通过独立 mem/DDR 通路；新增高吞吐外设应接 DDR UI 仲裁和 DMA/队列，使用必要的 CDC 连接控制面，不通过提高全局 sys 或软件反复搬运 CSR 实现带宽。

| 主题 | 文档 |
| --- | --- |
| 构建 | [目标与增量 PnR](build.md)、[硬核资源说明](gw5ast-hard-ip-usage.md) |
| 启动与操作 | [平台启动、装载与专项命令](operations.md)、[bootloader](bootloader.md)、[CPU JTAG](jtag.md) |
| 内存与接口 | [地址合同](address-map.md)、[cache](cache.md)、[DMA](dma.md)、[内存验证合同](memory-validation.md) |
| 板级设备 | [I/O 与中断](io.md)、[音频与麦克风](audio.md)、[HDMI](video.md)、[原生 SD](sd.md)、[用户 Flash/TWI](management.md) |
| 能力范围 | [已实现与未交付能力](capabilities.md) |
| 验收记录 | [内存验收](../../../reports/ae350-p2.md)、[DMA](../../../reports/ae350-dma.md)、[音频](../../../reports/ae350-audio.md)、[视频](../../../reports/ae350-video.md) |
| 性能 | [内存 baseline](../../../reports/performance/ae350-memory-baseline.md)、[板级 I/O](../../../reports/performance/ae350-board-io.md)、[读行优化](../../../reports/performance/ae350-read-line.md) |

共享规则见 [文档目录](../../README.md) 和 [组件构建](../../common/component-builds.md)。重复构建复用同一 workspace；板测使用准确产物身份与 SRAM/UART，执行交接和套件通过分别记录。
