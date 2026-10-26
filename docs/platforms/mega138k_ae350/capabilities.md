# AE350 能力与验证范围

适用机器：`mega138k_ae350`。具体配置由机器 JSON 决定；下表区分已实现路径与尚未交付能力，具体构建的通过范围以匹配验收报告为准。

| 功能 | 实现与边界 |
| --- | --- |
| 启动与内存 | Flash XIP bootloader、硬核 ILM/DLM、UART loader、零基址 DDR；见 [启动与操作](operations.md) 和 [内存验证合同](memory-validation.md) |
| DMA | 硬 DMAC 的 DDR/loopback 及音频 FIFO 通路；缓存与通道所有权见 [DMA](dma.md) |
| 板级 I/O | 板级 LED、按键/事件、WS2812、硬 GPIO 输入和 PLIC→CPU IRQ；风扇调速未交付，见 [I/O](io.md) |
| 音频与麦克风 | PT8211 TX、单路 I2S RX、硬 DMA 与并发诊断；数字链路与声学确认分开，见 [音频](audio.md) |
| HDMI | DVI 兼容 TMDS、640×480 RGB565、双缓冲及共享 VT 文字终端；见 [视频](video.md) |
| SD / 文件系统 / 完整 BIOS | [原生 SD](sd.md) 的 4-bit、硬 DMAC 只读门禁已过；文件系统和完整 BIOS 未交付；复用 VT 渲染器不代表已有完整 BIOS 或其二级程序 ABI |
| 网络 / USB Host / Linux | 未交付；芯片或板上具备相关资源不等于已有驱动和完整运行环境 |
| 用户 Flash / TWI | 硬核 SPI/I2C 接用户 U7 Flash 与 TWI mux；只读识别/访问见 [管理总线](management.md)，不含持久启动或下游设备配置 |
| XIP / Flash 安装 | U4 官方 0x600000 固件分区 boot XIP 与显式安装操作已实现；通用 0x88000000 窗口仍预留，板测/安装状态见 [bootloader](bootloader.md) |

HDMI 的 INDEX8、720p、HDMI 音频、DDC/EDID、CEC 与热插拔恢复不随 RGB565 视频验收自动成立。共享外设核心也不表示继承 20K 地址、缓存规则、频率或二进制兼容性。

验收依据见 [内存](../../../reports/ae350-p2.md)、[DMA](../../../reports/ae350-dma.md)、[板级 I/O](../../../reports/performance/ae350-board-io.md)、[音频](../../../reports/ae350-audio.md) 与 [视频](../../../reports/ae350-video.md)。
