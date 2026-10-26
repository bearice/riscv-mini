# AE350 官方 Flash 接口调查

2026-10-26。按用户要求先调查接口。本轮仅阅读官方工程、公开硬件模型和文档，没有切换控制器接法、运行擦写示例或写入 Flash。此前 sys 100 MHz 候选仍未通过完整 STA，不能作为已上板版本。

已确认：官方 `example/Flash` 提供的是同一 Flash 子系统的指令存储通路与 APB 寄存器通路。当前 U7 接的 ATCSPI200 也已经是硬核 SPI 控制器，并非软件模拟 SPI；二者不能只按名称混为一条接口。

| 项目 | 当前 U7 接法 | 官方 Flash 示例 |
| --- | --- | --- |
| 控制入口 | 硬核 SPI2/ATCSPI200，`0xf0f00000` | `FLASH_SPI_*` 与 `FR_*` |
| SDK 选择 | `ENABLE_SPI` | `ENABLE_SPI_FLASH` + `FlashRegisterRWMode` |
| 配置宏 | `AE350_APB_SPI_EN` | `AE350_EMB_SPIFLASH_EN` + `AE350_APB_FLASH_EN` |
| CPU 寄存器地址 | 固定硬 SPI 地址 | 示例把 Extended APB 回接给 FR，基址 `0xf8000000` |
| CPU 直接存储视图 | 现有实现未提供 | 指令 Flash 视图从 `0x80000000` 开始 |
| 示例物理引脚 | U7：L25/P20/R21/T22/R22/R20 | P18/H13/R14/R15/P14/N14，对应当前板的配置 Flash U4 |

接线与选择依据：[官方顶层](</home/bearice/workspace/fpga/TangPrimer-138K/RiscV_AE350_SOC_V1.3/example/Flash/ref_design/FPGA_RefDesign/DK_START_GW5AST138_V1.0/ae350_ext_flash/src/ae350_ext_flash.v:130>)、[引脚约束](</home/bearice/workspace/fpga/TangPrimer-138K/RiscV_AE350_SOC_V1.3/example/Flash/ref_design/FPGA_RefDesign/DK_START_GW5AST138_V1.0/ae350_ext_flash/src/ae350_ext_flash.cst:21>)、[IP 配置](</home/bearice/workspace/fpga/TangPrimer-138K/RiscV_AE350_SOC_V1.3/example/Flash/ref_design/FPGA_RefDesign/DK_START_GW5AST138_V1.0/ae350_ext_flash/src/riscv_ae350_soc/riscv_ae350_soc.ipc:17>)。

## 硬件接口怎样接

`FLASH_SPI_CSN/CLK/MOSI/MISO/WPN/HOLDN` 连接外部 NOR。寄存器通路 `FR_PCLK/FR_PRESETN/PADDR/PSEL/PENABLE/PWRITE/PWDATA` 来自 CPU Extended APB，`FR_PRDATA/FR_PREADY` 返回该 APB 总线。FR 端口没有导出 PSTRB、PSLVERR、IRQ 或独立 DMA REQ/ACK；不能据此宣称已有 Flash IRQ/DMA 验收。

示例直接回接整个 Extended APB，因为其总线上只有这个用途。当前工程在 `0xf8000000` 已有 SYSINFO，直接照抄会冲突。仓库已预留 `0xf8090000` Flash/XIP 管理页；若采用官方接口，需要页译码及必要的内部地址翻译，并保留 SYSINFO。FR 是否只译码低地址位，尚未验证，不能直接假定高位全部忽略。

公开 `AE350_SOC` 硬原语列出了 `SPI2_*` 和外部 `ROM_H*`，没有直接列出 `FLASH_SPI_*`/`FR_*`；后者由 SDK 的 SoC wrapper 和 `riscv_ae350_flash.v` 子系统提供。因此可以确认它是另一条 SDK 集成路径，不能仅凭顶层接口名称认定芯片中有第二个已证实的独立硬核控制器。未解密或修改厂商加密实现。

## 软件怎样使用

Flash 示例复用 `SPI_RegDef` 寄存器布局，区别在于基址是 `APB_EXT_BASE`。主要偏移如下。

| 偏移 | 寄存器 | 用途 |
| --- | --- | --- |
| `0x10` | TRANSFMT | 地址/数据宽度和格式 |
| `0x20` | TRANSCTRL | 命令、地址、方向、数据数量 |
| `0x24/0x28/0x2c` | CMD / ADDR / DATA | 命令触发、Flash 地址和 FIFO 数据 |
| `0x30/0x34` | CTRL / STATUS | 控制器/FIFO 复位及忙/空/满状态 |
| `0x40` | TIMING | 串行时钟分频和接口时序 |
| `0x50` | MEMCTRL | 存储器读访问控制及模式变更同步 |
| `0x7c` | CONFIG | FIFO 配置 |

官方驱动的顺序是：初始化控制器/FIFO，设置 3-byte 地址与 8-bit 数据格式，配置 TIMING；等待 SPI 空闲；读 MEMCTRL 并将原值写回，等待 bit 8 清零，随后通过 TRANSCTRL、ADDR、CMD、DATA 执行寄存器命令。驱动有读、页编程、4 KiB 和 64 KiB 擦除接口。源码没有给出 MEMCTRL 的完整位定义，不能把“写回原值”等同于一个已确认的通用 toggle 位，也不能将它当作 Flash 地址重定位寄存器。

参见 [flash.h](</home/bearice/workspace/fpga/TangPrimer-138K/RiscV_AE350_SOC_V1.3/example/Flash/ref_design/MCU_RefDesign/ae350_ext_flash/src/demo/flash.h:22>)、[寄存器结构](</home/bearice/workspace/fpga/TangPrimer-138K/RiscV_AE350_SOC_V1.3/example/Flash/ref_design/MCU_RefDesign/ae350_ext_flash/src/bsp/ae350/ae350.h:300>)、[初始化及模式切换](</home/bearice/workspace/fpga/TangPrimer-138K/RiscV_AE350_SOC_V1.3/example/Flash/ref_design/MCU_RefDesign/ae350_ext_flash/src/demo/flash.c:23>)。

默认 `BUILD_BURN` 的启动代码先从 Flash 把 loader 搬到 DDR，再把应用搬到 DDR 执行。`BUILD_XIP` 才使用 XIP 链接布局。不能把示例的 Flash 擦写函数原样放回正在使用的同一 Flash 中执行。上游 [Zephyr Andes XIP 驱动](https://github.com/zephyrproject-rtos/zephyr/blob/main/drivers/flash/flash_andes_qspi_xip.c) 将访问、写擦及相关准备函数标记为 RAM 函数，屏蔽中断和分支预测，并在修改后处理对应缓存。当前 bootloader 将代码和常量复制到硬 ILM 的方向与此兼容。

驱动也不能机械照抄：示例有无超时轮询，且 FIFO reset 的置位和等待掩码存在不一致（TX 置 bit 2 却等 bit 1；RX 置 bit 1 却等 bit 0），移植时需对照具体 IP 寄存器语义核实。示例 main 会擦写物理 `0x800000`，本轮没有运行它。

## 时钟、启动偏移与目前不能确定的部分

示例测试点为 CORE 800 MHz、AHB/APB 50 MHz，TIMING 的分频字段设置为 4，驱动注释给出的串行时钟关系为 `source / (2*(DIV+1))`。不能从此示例推断 sys 100 MHz 或复位时 XIP 的实际串行时钟已经通过。其 SDC 还单独约束了 Flash 子系统和 Flash pin clock，并使用模式互斥约束，需要按实际控制器模式审查，不能直接搬到当前工程。

示例烧录布局是配置位流物理 `0x000000`、固件物理 `0x600000`；CPU 的 Flash 视图/链接入口为 `0x80000000`。这证明示例采用这组配套布局，但不单独证明 RTL 内部一定采用固定 `+0x600000` 算法。

当前公开 IPC 参数没有看到 Flash start-offset/地址重映射选项，公开驱动也没有设置此类偏移。因此尚未确认官方 Flash 子系统能否将复位入口直接映射到 U4 的 `0x00ffe000`，更不能只改 linker/烧录地址就认定符合“末尾 8 KiB”。同样尚未确认能否在保留独立 U4 boot 后端的同时，把该子系统作为 U7 的独立控制器使用；它的标准配置是指令存储后端。

官方功能范围见 [MUG1031 §8.2.2](https://www.gowinsemi.com/upload/database_doc/2725/document_ja/69811021aaeed.pdf)：SPI Flash instruction memory 可以附加 Flash Register R/W Mode。当前 U7 普通硬 SPI 上的 JEDEC/SFDP 旧结果，不等于这个接口已经验收；本轮没有改变两颗 Flash 的控制器归属。

后续决策（2026-10-26）：用户接受按官方 0x600000 固件分区调整布局，撤销 U4 末尾 8 KiB 的位置要求。U7 保留硬 SPI；四线高速仅作为后续可评估项。以上未确认的内部 RTL 固定偏移不因此变成已验证事实，实施采用官方配套烧录/CPU地址合同。
