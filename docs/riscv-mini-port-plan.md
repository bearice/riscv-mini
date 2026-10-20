# AE350 平台移植说明

当前 138K 平台采用 AE350/A25 硬核、厂商 ITCM 启动镜像、硬核 ILM/DLM 和零基址 1 GiB DDR 地址合同。旧 fabric ROM/SRAM 最小系统与 P0/P1 过程文档已退役，历史实现从 snapshot commit 回溯。

- [平台架构与操作](ae350-platform.md)：启动、UART loader、诊断、恢复和时钟域。
- [地址、CSR、DMA、XIP 合同](ae350-address-map.md)：已实现地址与后续预留明确分开。
- [启动固件](ae350-bootloader.md)：供后续阶段共用的常驻 bootloader。
- [内存验证合同](ae350-memory-validation.md)：正确性、效率、CDC、完整性与性能口径。
- [最终验收报告](../reports/ae350-p2.md)：准确产物、回执、范围和限制。
- [硬核 IP 取舍](gw5ast-hard-ip-usage.md)、[后续路线](ae350-migration-stages.md)。

保留现有内容寻址构建框架和可分离的外设功能核心；CPU、原生 AHB/APB 接法与 DDR 后端采用 AE350 平台实现。SharedL2 不移植，fabric 外设控制与数据/DMA 通路分离。视频使用 HDMI，LCD 不纳入当前迁移。

板卡参考文件仅从 TangPrimer-138K/docs 与 example 读取。项目实现和文档位于 riscv-mini，不能把板卡参考目录当作实现工作树。摄像头引脚保持保留，CPU JTAG 走已分配的 Dock J23。

频率按当前器件/IP 配置和完整 STA/实板证据选取，旧 20K 频率不构成约束。当前数据口 100 MHz，外设 AHB/APB 与厂商 ITCM 80 MHz，DDR UI 100 MHz、物理 CK 400 MHz。2026-10-20 将 sys 设为 100 MHz 未过 STA：sys Fmax 84.8 MHz，ITCM 块 RAM 高脉冲 0.221 ns，要求 0.900 ns。800 MHz 是核心配置目标，counter 标定及适用范围另列证据，不从 PLL 参数推定性能。未通过的 133 MHz 尝试只定义该实现的失败边界。
