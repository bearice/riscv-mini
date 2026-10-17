# GW5AST-138 硬核资源与 AE350 平台使用取舍

日期：2026-10-17。范围：Tang Mega 138K Pro / GW5AST-138，实际 part、封装与硅修订仍需按实物锁定。本轮为资料/源码核对，未构建或上板。

本文属于 riscv-mini 项目文档。下文板卡 `example/` 与数据手册位于外部 [138K 配套示例](../../../TangPrimer-138K/example/) 和 [板卡参考资料](../../../TangPrimer-138K/docs/)；项目规划与结论留在本仓库。

## 1. 分类

硬核资源指已在硅片实现的处理器、协议/模拟宏和专用原语；厂商IP指工具能生成的实现，可能包含软逻辑、硬资源和wrapper。IP Core Generator里有某个名字，不等于芯片内有完整硬控制器。

“直接使用”表示选择/实例化已有宏，完成配置、引脚/时钟/复位/总线和软件接入，不重新实现其核心逻辑；仍需验收，相关wrapper/桥接/FIFO也可能消耗fabric。

## 2. 可优先使用的硬核

| 硬资源 | 已确认功能 | 138K项目取舍 |
| --- | --- | --- |
| AE350/A25 | CPU、MMU、32KiB I/D cache、64KiB ILM/DLM、调试 | 首版直接使用，替代Vex与旧L2启动结构；地址/初始化/cache维护单独验收 |
| AE350 PLIC/PLMT | 中断和机器timer；16个用户中断入口 | 替代Vex私有IRQ CSR和旧机器timer适配；统一由BSP提供 |
| AE350 UART1/2 | UART，兼容16550类寄存器，FIFO与DMA握手 | 主控台优先UART2，接BL616 N16/P15；无需再实例化LiteX UART，注意厂商示例和LiteX默认实现并不自动相同 |
| AE350 PIT/RTC/WDT/SMU | 定时、PWM相关功能、看门狗、时钟/电源管理 | timebase/周期中断直接复用；风扇PWM可评估PIT，tach计数和引脚驱动仍需实现；WDT在故障恢复可诊断后启用 |
| AE350 GPIO | 32路GPIO、方向和中断等 | 先驱动LED/按键/控制脚；自研GPIO只保留硬核不能满足的去抖/事件聚合或流接口，不为复用而强留重复控制器 |
| AE350 SPI/I2C | 硬外围控制器、FIFO/DMA相关能力 | 用于板级管理及适用的SPI设备；专用Flash boot路径与普通SPI外设要分别确认引脚所有权/模式，不能直接当成同一个接口 |
| AE350 DMAC | 多通道、硬件握手、链式传输 | 优先memcpy与audio/mic FIFO；导出DMA_REQ/ACK需显式启用，通道/请求路由及cache ownership仍需driver；不等于自动snoop |
| PLL、CLKDIV、DHCE、IOLOGIC、OSER/IDES、DDRDLL/DQS、IO delay | 时钟、串并转换、DDR采样/输出和延迟 | 直接由平台/PHY/output Adapter使用；HDMI、DDR、RGMII均可复用这些硬原语 |
| BSRAM/SSRAM | 块RAM、shadow/distributed存储资源 | FIFO、boot ROM、palette、少量buffer，按实际推断/实例化结果核算；framebuffer放DDR |
| DSP slices | 27x18等乘法、累加、级联/流水 | 编译器/综合或专门数据通路使用；不等同于CPU的DSP指令扩展，也不会自动加速C代码 |

AE350整套外围确为硬化子系统，官方Gowin/Andes发布说明确认。集成时应保留原硬核总线层级，不另造全套软UART/timer/GPIO来模拟旧机器；上层HAL由BSP/driver Adapter维持。

## 3. 有硬宏但不是首版必需

| 硬宏 | 板级条件与剩余工作 | 使用优先级 |
| --- | --- | --- |
| 8路高速SerDes PMA/PCS（封装相关） | Pro的FPG676A资源表列8路；板路由分配给PCIe/SFP等，不能自由分配任意GPIO；有CDR/8b10b等，参考时钟需配置外部MS5351和相关路由 | 后续SFP+/PCIe使用；与HDMI的GPIO OSER不是一回事 |
| PCIe控制器硬核 | 新DS1239列Gen1/2/3、Endpoint；本地早期DS1104列PCIe2.0且部分功能描述不同。Pro有PCIe接口与Gen2/3 DMA例程，但DMA、BAR后端、地址映射和host driver不自动由硬核提供 | 后续PCIe Endpoint；不能由此承诺AE350可直接作为NVMe Root Complex |
| MIPI D-PHY RX | FPG676A资源表列8 data+2 clock lane，板上两个CSI接口实际lane/接线需按原理图核对；CSI-2包处理、像素解包和framebuffer并非PHY本身 | 摄像头后续目标，不用于当前HDMI TX |
| 2个ADC | delta-sigma低速采集，支持内部温度/电压监测和外部模拟输入；外部量程按数据手册/UG299，不能当普通3.3V GPIO或音频ADC | 可用于温度/电压遥测，先验证内部传感器；不替代I2S麦克风/音频codec |
| 配置/JTAG/DNA/OTP等 | 配置、调试、唯一身份等硬件设施；Flash本身在板外 | 下载/恢复/读取身份按需使用；OTP/安全位不作为移植步骤 |

## 4. 容易误认成完整硬核的项目

| 名称 | 实际组成 | 对当前工作的影响 |
| --- | --- | --- |
| Gowin DDR3 Memory Interface | controller/训练/接口逻辑结合DDR专用IO原语；官方手册说明Verilog实现并列LUT/REG占用 | 可直接复用厂商IP实现，但不能按“零LUT硬DDR控制器”预算；Shared DDR模式/独立IP两条路线仍需选定 |
| HDMI/DVI TX | TMDS编码、视频时序/控制由逻辑实现，使用硬PLL/OSER/IO | 没有可直接替代整条scanout的硬HDMI controller；优先复用simple_video_out或厂商DVI TX，适配framebuffer/stream |
| 千兆/10G Ethernet MAC | MAC是需综合的IP；SerDes硬PCS只覆盖相应物理/编码功能；RJ45的RTL8211是板外PHY | RGMII MAC/ring/driver仍需接入，不会因AE350或SFP存在而自动获得网络 |
| USB Host/Device/SoftPHY | 厂商Host控制器手册明确Verilog实现；Pro配套Device用FPGA SoftPHY/RC网络；BL616 USB属于独立MCU | 可评估复用IP，但不是芯片内置USB硬核控制器，Host的PHY/供电/协议支持独立确认 |
| SD/MMC Host | AE350硬外设列表没有通用SD Host；厂商APB SD Host属于扩展实现 | 接入SD controller/driver，再用DMAC或自带数据搬运路径；不能只接几根硬GPIO就算完成 |
| I2S/PT8211、mic接口、WS2812、framebuffer reader | 由fabric逻辑或对应软IP实现 | 正是现有功能core值得复用的部分，控制/供数可交给硬GPIO/DMAC/总线 |

## 5. 当前推荐组合

首版：AE350 CPU/cache/MMU + PLIC/PLMT/PIT + UART2 + 硬GPIO/SPI/I2C + DMAC；平台提供必要桥接与存储；DDR使用验证过的IP/硬IO；视频用TMDS encoder + OSER + PLL，先单路输出。

保留fabric实现：PT8211 TX、mic I2S RX、WS2812、视频pixel/scan/reader、FIFO/CDC、必要SD/Ethernet/USB模块。把控制与搬运交给AE350，但不假设硬DMAC能自动代替每一个专用实时reader。

后续：ADC遥测可小规模加入；PCIe/SFP/MIPI按独立目标接入。首轮不为“用满硬核”而启用尚无需求的接口。

阶段计划调整：P1使用硬UART/timer/IRQ并评估硬GPIO；P3接硬DMAC；P4重点验证GPIO BSP和必要事件逻辑，跨平台模块复用试点可转为WS2812/mic；P6使用硬PLL/OSER和soft TMDS/scanout。厂商IP只是替换某项实现，仍保留输入锁、模块接口和设备/机器合同。

## 来源

- [GW5AST新数据手册DS1239-1.0.3E](https://cdn.gowinsemi.com.cn/DS1239E.pdf)：硬AE350、SerDes、PCIe、MIPI、ADC与资源表。与本地DS1104旧版差异显式保留。
- [Gowin/Andes硬化AE350子系统说明](https://www.gowinsemi.com/en/about/detail/latest_news/81/)。
- [AE350硬件设计手册](https://www.gowinsemi.com/upload/database_doc/2725/document_ja/69811021aaeed.pdf)：外围列表、扩展接口方向与启用配置。
- 本地 `example/ddr_test/docs/IPUG281-2.2E.pdf` 第3.3/4章及 `riscv-mini/.venv/.../litedram/phy/gw5ddrphy.py`：controller实现与专用IO原语。
- [DVI TX/RX资源说明](https://www.gowinsemi.com/upload/database_doc/2720/document/68961f5c39887.pdf)。
- [USB Host资源说明](https://www.gowinsemi.com/upload/database_doc/2730/document/66222231be36c.pdf)。
- 本地 `simple_video_out`、`ae350_customized_demo`、`udp_rgmii_send`、`usb2_soft`、`pcie_dma_demo`、`sfp+/customized_phy` 及底板原理图。
