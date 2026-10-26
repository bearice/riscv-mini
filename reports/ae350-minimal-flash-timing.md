# AE350 官方 Flash 最小系统时序探测

后续状态：用户已正式选择完整系统sys50并清理提频结构，完整PnR通过，见[sys50简化记录](ae350-sys50-simplification.md)。本文末尾的“恢复sys80”描述当次探测完成时的状态。

2026-10-26。按用户要求先测 100MHz，失败后在同一个 `default` gateware workspace 串行复验 80MHz。统一入口为 `mini.py build gateware --machine mega138k_ae350 --synthesize`。

仅实例化 AE350 硬核、当前官方 SDK Flash（FR APB 寄存器接口开启）、PLL/复位、UART/JTAG 引脚。FR 按官方示例直接回接扩展 APB；硬核外部数据口为常数终结，不构建 DDR、SD、音频、视频、SYSINFO 或应用运行环境。CPU 保持 800MHz，AHB/APB 为所测 sys 频率。该探测位流不可用于实板运行，板端入口显式拒绝。

| sys | setup 违例 | hold 违例 | 列出的最差 setup slack | 最小脉宽 slack | 结论 |
| --- | ---: | ---: | ---: | ---: | --- |
| 100MHz | 8883 | 0 | -6.570ns | +0.130ns | 失败 |
| 80MHz | 3637 | 0 | -4.157ns | +1.237ns | 失败 |
| 50MHz | 0 | 0 | +0.127ns | +4.987ns | 通过 STA |

100MHz 最差路径为 SDK Flash `ctrl_cs_r` 到 TX FIFO `empty`，数据延迟 16.482ns；80MHz 同样包含 Flash 仲裁到 TX FIFO 的长路径，另有上升沿到下降沿路径 slack -2.265ns。工具报告的该次 sys Fmax 分别为 60.349/58.717MHz；这些数字属于具体实现结果，不能当作芯片或所有 SDK 模式的固有上限。

最小系统资源为约 6276 个逻辑单元、8831 个寄存器、1 个 AE350_SOC，无 BSRAM。结果排除了 BSRAM、DDR/SD/音视频胶合逻辑作为这些 Flash 违例的必要条件；当前启用 FR 的官方 Flash 实现仍是时序问题所在。没有测试关闭 FR 的只读 XIP 配置，也没有通过模式假设或 false path 隐藏内部路径。

100MHz run：`20261026T011925577986Z-build-minimal-sdk-flash-sys100-6ad448`。

80MHz run：`20261026T012523054122Z-build-minimal-sdk-flash-sys80-a14e12`。

用户随后要求确认 50MHz，同一最小系统在 run `20261026T043739663184Z-build-minimal-sdk-flash-sys50-e10cd6` 通过完整 STA。AHB/APB 均为 50MHz，CPU 仍为 800MHz；工具报告 sys Fmax 50.319MHz，最紧 setup slack +0.127ns。成功 gateware artifact 为 `75f99d3ee79c5022857ce5883d47af9119981ac30fcf3fa8966519536a5d3a9d`。它仍是不可上板的时序探测产物，不代表完整系统的 DDR/SD/音视频时序或实板功能已通过。此次仅探测，没有把主配置改成 50MHz；测完恢复完整系统 sys80。

各 run 保存完整时序报告、资源报告、生成顶层、SDC 和实际配置；机器配置已退出探测模式，并按用户要求保留 sys80。完整系统的 80MHz XIP 组合尚未通过 PnR/板测，未覆盖已验收板上版本，未写 Flash。bootrom `d96c9542d1ad` 在两次探测中均复用。

配置/构建回归 11+21 项和探测配置/实板隔离 2 项测试通过。详细数值见[机器可读结果](ae350-minimal-flash-timing.json)。
