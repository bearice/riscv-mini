# 20K 系统设计

适用机器：`primer20k_vexriscv`，板卡为 Tang Primer 20K + Dock 3713。硬件功能、启动模式、CPU 特性与时钟来自机器 JSON；软件使用所选硬件的导出。本文说明各路径的设计，不声明所有功能在当前 JSON 中都已启用。

## CPU、启动与内存

VexRiscv 单核通过 Wishbone 和共享 writeback L2 访问 LiteDRAM。MMU、FPU、C 扩展、缓存与流水线由 `ip.cpu.parameters` 驱动生成，见 [CPU 能力](cpu-mmu-fpu.md)。

ROM 与 Flash XIP 是两种启动后端，由 `boot` 配置选择。启动代码使用 L2 中固定的 4 KiB RAM 建立栈/data/BSS，执行软件 DDR 初始化和读训练；DDR 就绪后解除固定，在同一地址继续运行。随后从 Flash 应用分区或 UART 装载应用，验证范围、ABI 与 CRC，再跳转至 DDR。没有独立工作 SRAM，见 [DDR 启动](ddr-boot.md) 和 [bootloader](bootloader.md)。

BIOS 提供 POST、终端、设备服务和 SD/TFTP 载荷装载；可用能力取决于硬件与匹配软件。RPB 应用可返回 BIOS，OSB 路线通过 OpenSBI 交接，见 [BIOS](bios.md) 和 [OS 引导](opensbi-port.md)。

## 地址与中断

内存布局由 `gateware/machines/primer20k_vexriscv/address_map.py` 从机器内存参数导出。下表为零基址、128 MiB DDR 配置的布局；实际软件使用生成头和链接输入。

| 区域 | 地址 / 规则 |
| --- | --- |
| DDR | `0x00000000..0x07ffffff` |
| 启动栈/data/BSS | `0x007ff000..0x007fffff`，训练前由 L2 RAM 提供 |
| BIOS / DDR 应用 | `0x00800000` 起，4 MiB 窗口 |
| 二级程序 | `0x01000000` 起，范围由载荷合同限制 |
| CSR 外设 | `0xf0000000` 起，以生成 CSR 表为准 |
| Ethernet packet SRAM | `0xf1000000` 起；ring 模式仅 DMA 私有访问 |
| USB Host | `0xf2000000` 起 |
| Flash XIP | `0xf3000000..0xf33fffff`；物理偏移 1 MiB 对应入口 `0xf3100000` |

IRQ 编号来自实际实例化的 LiteX 导出，不硬编码旧 full 组合的顺序。MMU 配置的 machine timer 使用 MTIP，S-mode 外设 mask/pending 适配见 OpenSBI 文档。

## 设备数据路径

CPU/音频使用 32-bit Wishbone，LCD 使用 16-bit 像素流，SD/Ethernet DMA 使用带缓冲的内存端口；内存控制器转换到共享 L2 的 coherent 128-bit 入口。L2 可见性不包含 CPU 私有 L1 snoop，DMA 所有权交接仍须软件维护，见 [内存控制器](memory-controller.md)、[L2](l2-cache.md) 和 [DMA](native-dma.md)。

Ethernet 可选 CPU PIO 或独占描述符 ring；USB 可选 Ultra PIO 或 OHCI。显示使用 DDR 帧缓冲，音频使用 DDR ring/FIFO，麦克风提供有限长度快照。具体功能与限制见 [平台索引](README.md)。

## 配置、构建与验收

CPU 参数在 `ip.cpu.parameters`，设备、后端和容量在 `rtl`，启动方式在 `boot`；配置依赖见 [配置说明](configuration-profiles.md)。

```sh
python scripts/mini.py plan system --machine primer20k_vexriscv
python scripts/mini.py build system --machine primer20k_vexriscv --synthesize
```

重复构建复用同一 workspace；PnR、固件功能与性能分别验收。具体镜像和资源从所选产物及收据读取，不沿用旧候选的容量、资源或通过结论。日常上板使用 [20K 操作入口](operations.md) 的 SRAM/UART 流程；Flash 只在明确请求时写入。

## 能力边界

- 软件 DDR 训练和 L2 启动 RAM 是启动路径的一部分，不能按其他板卡或历史镜像推断就绪。
- USB Ultra 支持全速 HID 与约定 Hub 拓扑；低速、高速数据模式和 MSC 不在支持范围，见 [USB](usb-light.md)。Flash 启动后的间歇枚举失败仍需按专项说明处理。
- PT8211 是输出 DAC；麦克风为快照输入，不提供连续录音 DMA。
- HDMI 和 RTOS 不属于本机器的已交付运行环境；20K Linux 的资格见 [Linux](linux-port.md)。
