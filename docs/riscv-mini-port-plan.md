# Tang Mega 138K Pro / AE350 移植方案

更新：2026-10-17。本文只描述当前决定与实施状态。原始板卡资料保存在外部[docs](../../../TangPrimer-138K/docs/)和[example](../../../TangPrimer-138K/example/)；项目规划与验收记录保存在riscv-mini。

## 当前决定

使用内置AE350/A25硬核，重建启动、时钟/复位、总线、DDR、IRQ、cache维护和DMA。首版不引入SharedL2或pinned-L2启动栈。复用`mini.py build/plan`、组件依赖图、不可变store、工具/输入锁与复现机制。

CPU、总线和DDR按新平台规格测试上限：CPU目标800MHz，当前官方DDR接口CK目标400MHz（800MT/s），数据内存总线和AHB/APB严格低于200MHz，AHB/APB同步1:1。先独立表征各域，再验证完整组合；规格、PnR与实板稳定上限分开记录。验收方法见[频率上限测试](ae350-migration-stages.md#频率上限测试)。

优先使用硬UART、timer、PLIC、GPIO与DMAC。外设按功能core、寄存器控制、数据搬运和平台组装分离。可复用PT8211 TX、mic I2S、必要去抖/事件、WS2812和通用像素/调色板/framebuffer/换帧逻辑；总线、IRQ、时钟、DMA与cache ownership由AE350平台接入。

138K视频使用HDMI连接器上的TMDS输出，LCD迁移暂缓。先单路独立测试图，再DDR framebuffer；首版不包含HDMI音频/CEC/EDID协议，音频继续PT8211。SD、网络、USB和Linux按独立阶段接入。

## 硬件事实与待确认项

| 项目 | 当前配置 |
| --- | --- |
| 板卡 | Tang Mega 138K Pro；实物核心板/底板修订、FPGA完整part尚未读到 |
| 参考器件 | GPRJ为GW5AST-138B / GW5AST-LV138FPG676AC1/I0，不能替代实物确认 |
| 输入时钟 | 50MHz，P16 |
| DDR | 照片识别两颗SK hynix H5TQ4G63EFR-RDC；x32、预期1GiB、15行位/10列位/8 banks；寻址与时序待P2 |
| UART | 板卡参考RX N16 / TX P15；customized demo为V16/U16；P1落实实际路由 |
| 复位 | 核心板U4/LVCMOS15；customized demo为G16/LVCMOS33；不能直接搬用示例CST |
| Flash | 两颗分别接线的128Mbit NOR；分别确认JEDEC、用途、引脚所有权及布局 |
| audio | PT8211-S：BCK E22、WS F24、DIN G24、PA_EN H17 |
| mic | J28 MIC-ARRAY，4路数据加BCK/WS，与PMOD0/SSPI复用；需外接模块 |
| HDMI | simple_video_out及cam2hdmi_alt提供参考实现，实际引脚/时钟/serializer需验证 |
| 网络 | RTL8211FI RGMII；MAC、MDIO、时钟/延迟及软件栈需接入 |
| USB | 板载SoftPHY配套示例为Device；Host独立验证PHY、供电和协议能力 |

## 构建与兼容边界

Board记录物理资源、电气条件和装配；Machine选择CPU、存储、时钟、地址/IRQ及执行合同。板型、硬核、厂商HDL、IPC、CST、SDC、初始化数据、构建实现和工具身份进入准确输入闭包。硬核CPU产物记录配置及依赖。

138K引用按机器隔离；不能回落到现役20K引用。更换平台默认重新编译固件。HAL源接口、设备ABI、机器执行合同、运行时ABI和加载格式分别定义兼容性，见[多平台架构](multi-platform-architecture-proposal.md)。

DDR集成候选为独立Gowin DDR IP/AHB路径。CPU/fabric共享DDR端口及仲裁在P3落实。Extended AHB Master不能当作通用DDR入口；硬DMAC也不自动提供cache snoop，必须落实buffer所有权和clean/invalidate。

## 实施状态与顺序

P0主机构建底座完成，支持machine-description和hard CPU配置，真实输入归档与REUSE验证通过。DDR丝印已确认，可用RAM范围待P2；FPGA完整part及板卡版本继续保留为待确认。[操作说明](ae350-p0-build-foundation.md)和[P0验收记录](../reports/ae350-p0.md)保存具体证据。

当前推进P1：不依赖DDR的ROM/SRAM最小系统已完成PnR；ROM字节读取已修复，CPU JTAG及JTAG辅助的ROM读回、UART控制台、ebreak与再次加载恢复已通过。额外UART诊断逻辑已移除。timer/IRQ、自主启动横幅捕获、CPU实际频率与上限压力表征仍待验收，详见[当前上板记录](../reports/ae350-p1.md)。customized demo的CPU复位等待DDR校准，不满足独立启动要求。

完整顺序：P0身份/构建 → P1最小启动 → P2 DDR/cache → P3共享访问/DMAC → P4 GPIO/共享核心 → P5 audio/mic → P6 HDMI → P7 SD/BIOS → 可选P8网络、USB/Linux → P9组合负载、性能和归档。交付及量化门槛以[分阶段计划](ae350-migration-stages.md)为准。

Flash写入和断电冷启动分别仅在用户明确要求后执行。主机生成、仿真、PnR、实板功能及物理观察分开记录，未执行项不标通过。硬核取舍及来源见[GW5AST硬核资源](gw5ast-hard-ip-usage.md)。
