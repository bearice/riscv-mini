# Tang Primer 20K / VexRiscv

机器 `primer20k_vexriscv`，板卡 `primer20k_dock3713`（Tang Primer 20K + Dock 3713）。VexRiscv 软核经共享 L2 / LiteDRAM 访问 DDR；启动代码完成软件 DDR 初始化，然后从 Flash 或 UART 装载 DDR 应用。常驻 BIOS、RPB/OSB 载荷与 Linux 路线属于本机器。

当前构建参数来自 [Machine JSON](../../../gateware/machines/primer20k_vexriscv/machine.json) 与 [Board JSON](../../../gateware/boards/primer20k_dock3713/board.json)。文档中的 full、XIP、MMU/FPU 与资源数字若附带历史身份，只表示该配置的能力或验收；不能替代当前 JSON 和产物身份。

板载NOR容量由Board的 `resources.flash.size_bytes` 声明，当前为8 MiB；构建导出 `flash_size_bytes` 和 `CONFIG_FLASH_SIZE`。平台的4 MiB XIP/镜像窗口与写入分区按[启动布局](bootloader.md)执行。

| 主题 | 文档 |
| --- | --- |
| 系统与板卡 | [系统设计](system-design.md)、[板级参考](board-reference.md)、[时钟](clocks.md) |
| 构建配置 | [组件构建](build.md)、[CPU / SD / 音频参数](configuration-profiles.md)、[CPU 能力](cpu-mmu-fpu.md)、[RTL 转换](rtl-defaults.md)、[BSRAM](bsram-audit.md) |
| 启动与操作 | [板端操作与更新](operations.md)、[bootloader](bootloader.md)、[DDR 启动](ddr-boot.md)、[现场连接](lab-environment.md) |
| 内存与接口 | [共享内存控制器](memory-controller.md)、[L2](l2-cache.md)、[DMA 路径](native-dma.md)、[CSR 打包](csr-packing.md)、[构建契约与 BIOS ABI](contracts.md) |
| 固件 | [HAL](hal.md)、[BIOS](bios.md)、[二级程序移植](bios-payload-porting.md)、[Newlib](payload-libc.md) |
| 存储与网络 | [SD](native-sd.md)、[Ethernet](ethernet.md)、[Ethernet DMA](ethernet-dma.md) |
| 显示与输入 | [RGB LCD](video-spec.md)、[USB](usb.md)、[轻量 USB](usb-light.md)、[键鼠示例](usb-input-demo.md) |
| 音频与录音 | [PT8211](audio.md)、[I2S 麦克风](microphone.md) |
| OS 引导 | [OpenSBI](opensbi-port.md)、[Linux](linux-port.md)、[U-Boot 显示](uboot-framebuffer.md)、[U-Boot Ethernet ring](uboot-ethernet-ring.md) |
| 应用 | [DOOM](doom.md)、[GW-BASIC](gwbasic.md)、[Nyan Cat](nyancat-demo.md) |
| 验收与性能 | [固件测试](firmware-tests.md)、[BIOS benchmark](benchmark.md)、[发布示例](release.md) |
| 版本 | [发布里程碑](version-history.md) |

共享规则见 [文档目录](../../README.md) 和 [组件构建](../../common/component-builds.md)。重复构建复用同一 workspace；日常板测使用 SRAM/UART，写 Flash 必须得到明确请求。
