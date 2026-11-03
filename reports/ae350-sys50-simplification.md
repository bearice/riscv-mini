# AE350 sys50 完整系统简化

2026-10-26。用户决定 sys/AHB/APB 固定为 50MHz，撤掉提频实验带来的复杂性。CPU800、mem/DDRUI100、DDRCK400 和 SD 的 mem100 域保留；未来大数据外设直接接入 DDR 数据通路及其仲裁，控制面保留在 sys50。

音频核心及 FIFO 回到 sys50，删除 audio80 域、额外 PLL 输出、音频 APB/AHB CDC；DDS 仍提供平均 48kHz，三帧3125个sys周期。删除额外 AHB response relay，恢复 HSEL 限定的地址捕获；保留既有 AHB 事务所有权和注册派发。APB 删除额外译码阶段及逐页读回寄存器，保留命令捕获、一次提交和最终响应注册；SD/Flash 仍保持事务直到原生完成。视频范围检查回到组合逻辑，不再需要为它预热两级流水；有效 stride 的宽度和地址范围限制保留。移除温度/风扇/视频算术的强制 logic 推断属性，允许综合器使用 DSP。

保留的 CDC 服务真实独立时钟域：CPU 数据口 mem100→DDRUI100、sys50→SD mem100、视频像素/DDR 和状态同步。HDMI reset 10ns 相移修复保留，它避开125MHz边沿，是正确性要求。启动合同、FR管理页、Flash物理0x600000布局和U7硬SPI接法保持。

[主机/事务/物理音频回归](ae350-sys50-simplification-tests.json)与[PLL/视频/HDMI RTL回归](ae350-sys50-verilog-tests.json)已通过。bootrom 应复用 `d96c9542d1ad`；硬件 ABI 和 payload 必须消费准确的新 gateware。

完整 PnR 在原 `default` 工作区通过，run `20261026T062546792531Z-build-sys50-simplified-full-system-0c55fc`。gateware 为 `eaebc3f795263b06f3fb2395c5a7fcc6dea183e5226279c21f75b2c287c25d59`，bootrom `d96c9542d1ad` 正确复用。

setup/hold 违例为0/0，最小脉宽裕量+0.038ns。列出的最紧setup为mem100的DDR事务结束路径，裕量+0.013ns；没有因降sys而放宽DDR/SD域约束。工具本次报告sys Fmax52.404MHz、mem100.125MHz、DDRUI101.260MHz。保留现有mem100的50ps下降沿微调，PLL模型校验5.05/4.95ns实际波形。

布局后资源为23654逻辑单元、30550寄存器、45个BSRAM，DSP占用4.5/298。音频边沿量化现为20ns；SD卡时钟仍可由mem100产生50MHz。新配置的SD/DDR吞吐、音视频并发和自主Flash启动未做实板验收，不能继承旧sys80测速或板测结果。

构建阶段未编程板子、未写Flash、未改变current。此后用户明确授权部署，已完成bootrom与配置位流安装、完整读回及Flash重载启动，见[实板安装记录](ae350-sys50-board-install.md)。具体构建身份、时钟和结果见[构建摘要](ae350-sys50-simplification.json)。

## 2026-11-03 后续：mem 并入 DDR UI

用户采纳合并：mem 域（CPU 数据口 `DDR_CLK`、互连、DDR 适配器 AHB 侧、SD）改由 DDR IP 的 UI 时钟驱动，上文保留的 mem100→DDRUI100 CDC 与 mem100 的 50ps 微调随 fabric PLL 第三路输出一起删除；sys50→SD mem 的 CDC 保留。DDR 适配器的请求/响应队列改为同时钟 FIFO。用户同时决定不为 DDR 时钟不可用的情形另设兜底：校准超时后 CPU 仍从 Flash/ILM 启动，但访问 DDR 可能挂住。

PnR run `20261103T041525542162Z-build-mem-on-ddr-ui-clock-hdmi-lane0-site-f0262d`，gateware `24a6adbfdf2e`：setup/hold 0/0，最小脉宽余量 0.173ns，DDR UI Fmax 100.244MHz，image ABI 1350241324 不变，寄存器少约 670。首次 PnR 只在 HDMI lane 0 复位释放路径失败（时钟网络重排使 `hdmi_reset_clk` 树慢 0.194ns），把该路触发器从 R40C181 移到其 OSER10 所在的 R35C181 后通过。Gowin 会把腾出的 PRIMARY 自动分给 `ddr3_clk_ref`，报告仍为 8/8。

实板（SRAM，对照 `7522f3e5`）：ddr-suite 10/10，关 cache 的 DDR 访问周期少约 6%（指针追逐每跳 1623→1521 cycles，纯写带宽样本快约 17%），`20261103T035523501371Z` 对 `20261103T043433887777Z`；dma-suite `20261103T044219538249Z` 通过；Linux `--devices` `20261103T043635177186Z` 通过，USB 0 重发；SD 自动启动 `20261103T043715125462Z` 通过（U-Boot 读 Image 4.6 MiB/s）。
