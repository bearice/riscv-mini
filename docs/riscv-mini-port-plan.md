# riscv-mini 移植到 Tang Mega 138K Pro 的评估

评估日期：2026-10-17。来源为本地 docs、example、TangPrimer-20K/riscv-mini 当前源码和其已安装的 LiteX/LiteDRAM 依赖。本文是实施方案，尚未编译 138K bitstream 或执行 138K 实板测试。

本文及配套规划属于 riscv-mini 项目文档。下文参考资料中的 `docs/` 和 `example/` 分别指 [138K 板卡参考资料](../../../TangPrimer-138K/docs/) 和 [配套示例](../../../TangPrimer-138K/example/)；该外部目录保留原始板卡资料，不放项目分析或移植规划。

## 当前采用的架构方向

频率策略更新：138K的CPU、总线与DDR按新平台规格和实际实现测试上限，不承担旧板卡60MHz sys/120MHz DDR CK的频率兼容要求。分别表征CPU、总线、DDR，再验证组合负载；低频点仅用于定位问题。具体时钟边界、扫频方法及验收见[分阶段计划的频率上限测试](ae350-migration-stages.md#频率上限测试)。

最新视频范围：138K使用HDMI输出，LCD相关移植暂时搁置。以下早期LCD复用/面板分析保留作对照，不能作为138K当前任务；现有视频逻辑仅评估复用通用像素、framebuffer与换帧功能。当前实施验收以分阶段计划的HDMI P6为准，PT8211音频输出保持独立。

2026-10-17 用户明确提出：复用现有构建框架，从 CPU 到总线基本更换，选择性复用 audio、mic、GPIO、LCD driver 等模块。后续方案以此为边界：AE350 为新平台的 CPU 基础，重新建立启动、时钟/复位、总线、DDR、IRQ、缓存与 DMA 接口；首版不带入旧 SharedL2 和 pinned-L2 启动栈。前期“保留 VexRiscv 与旧共享内存系统”的分析保留为技术对照，不再作为首版目标。

组织结构与分层兼容性方案见 [多平台组织、外设解耦与兼容性设计提案](multi-platform-architecture-proposal.md)。该提案区分 Board/Machine、外设功能核心/控制/数据搬运，以及硬件 ABI、运行时 ABI、镜像格式；尚未实施。

当前AE350路线的实施顺序、资源预算和量化验收以 [AE350分阶段移植与验收计划](ae350-migration-stages.md) 为准。新核对的底板第16页包含J28 MIC-ARRAY接口（4路数据+BCK/WS，与PMOD0复用）；板上仍需外接麦克风模块。原文后面的VexRiscv阶段表仅作对照。

构建侧保留 `mini.py build/plan`、组件依赖图、内容寻址产物、工具身份、构建锁和源码复现机制。CPU 组件应能表示硬核器件/实例配置，不再强制等同于 VexRiscv Verilog + generator.json。板型、硬核配置、DDR IP、总线映射与软件平台合同共同进入组件身份；更换硬件平台后不默认复用旧 firmware 二进制。

可复用外设按协议核心、FIFO/stream、控制寄存器与总线/DMA 适配拆开评估：

| 模块 | 候选复用内容 | 新平台必须处理的边界 |
| --- | --- | --- |
| audio | PT8211 串行发送、采样时序、FIFO、静音/功放控制逻辑 | 目前类内混有 Wishbone DDR ring reader；替换/重接 DMA、RAM 范围、时钟和寄存器接口 |
| mic | I2S 接收、采样拼接、snapshot FIFO | 外接麦克风的物理连接、采样时钟、IRQ/寄存器；不假定 138K 底板自带对应数字麦克风 |
| GPIO/WS2812 | 去抖、输入状态/事件、输出与灯带时序 | 新板引脚、电压、有效电平、事件到中断路由 |
| LCD driver | 像素格式、调色板、扫描时序/输出、FIFO、换帧等已实现功能 | 当前 video.py 有固定尺寸与内存端口依赖；面板参数、DDR reader/DMA、CDC、寄存器及 cache ownership 需适配 |

不要求一次性泛化全部模块。优先接通 AE350 最小系统，再按 GPIO → audio/mic → HDMI 等可独立验收的单元逐个迁入；SD/网络/USB另行确定接入方案。P0构建底座与硬核配置描述已落实，见[P0验收记录](../reports/ae350-p0.md)；实际总线/DDR/DMAC集成仍需后续实现和验证。

## 前期保留 VexRiscv 路线的对照

原建议为保留 60 MHz sys、128 MiB 软件可见 RAM、16 字节 L2 行与窄 DMA 端口，以 GW5DDRPHY 120 MHz DDR CK / 1:2 / DLL-off 验证，官方 400 MHz IP 用作对照和备选。以下涉及该路线的训练、共享 L2 与分阶段表格均不代表当前 AE350 首版实施要求。所有路线都尚未获得本轮实板资格。

## 已核实的资料差异

| 项目 | 当前 20K | 138K Pro 本地资料 | 移植含义 |
| --- | --- | --- | --- |
| FPGA | GW2A-LV18PG256C8/I7 | 示例既有 GW5AST-LV138FPG676AES，也有 GW5AST-LV138FPG676AC1/I0，均为 138B | 先确认实物型号，不能固定使用旧 ES 器件号 |
| 输入时钟 | 27 MHz | 核心板 50 MHz 振荡器，P16 | 使用 GW5APLL，重新生成时钟和复位约束 |
| UART | RX M11 / TX T13 | FPGA RX N16 / TX P15，连接底板 BL616 调试器 | UART 协议可保留，设备与下载链需要重新识别 |
| DDR | H5TQ1G63EFR x16，128 MiB，120 MHz | 两片 x16 并联为 x32；零售 Hynix 示例为 15 行位 / 10 列位 / 8 bank，推导总容量 1 GiB | 从 2 个 byte lane 变成 4 个，BL8 变成 32 字节 |
| DDR 装配差异 | 当前已确认颗粒 | Micron 定制示例为 14 行位，推导总容量 512 MiB；早期原理图颗粒标注也不宜代替实物 | 必须按实物选择 DDR 几何与时序；只开放 128 MiB 也不能选错物理几何 |
| 配置/数据 Flash | 当前板实测 8 MiB，既有布局 | 原理图两个 128 Mbit NOR，MSPI 与另一组 QSPI 信号分别连接 | 两颗不能直接当成连续 32 MiB；需要分别确认 JEDEC、用途和引脚所有权 |
| 网口 | RMII 100M | RTL8211FI，RGMII | PHY、DDR IO、时钟及 MDIO 配置需更换 |
| USB | USB3317 ULPI 转 serial FS Host | SOFT_USB FPGA RC 网络；配套示例是 USB Device；另一个 USB-C 接 BL616 调试器 | 无现成等价 USB3317 通道，不能直接迁移当前 ULPI 初始化 |
| RGB LCD | 480x272 RGB565，完整 HS/VS/DE 接口 | 480x272 与 800x480 示例，输出 6 bit 每色及 DE/CLK | 内部 RGB565 和 DMA 可保留，末端引脚、色位、控制信号与面板时序需适配 |
| 音频 | PT8211-S | 同为 PT8211-S，BCK E22 / WS F24 / DIN G24，PA_EN H17 | 现有数据格式与 DMA 复用价值高 |
| 麦克风 | 外接 I2S 数字麦克风 | 音频原理图耳机插座 MIC 未形成可用录音 ADC 路径 | 首版关闭 mic；需要另接设备才能恢复 |

特别注意：U4 核心板用户按键上拉至 1.5 V。已有 led/audio 示例把 U4 写为 3.3 V，不能照抄；本地 LiteX 平台使用 LVCMOS15。底板按键则需要按各自引脚单独判断。原理图文件名含 4071f，但底板标题栏仍出现 4071d/Rev1.11；应以实物 PCB 丝印及关键网表对照确认版本。

## 可用的现有基础

当前 `.venv/Lib/site-packages` 已有：

- `litex_boards/platforms/sipeed_tang_mega_138k_pro.py`：时钟、UART、DDR、LCD、SD 等资源。
- `litex_boards/targets/sipeed_tang_mega_138k_pro.py`：GW5APLL、DHCE/CLKDIV、GW5DDRPHY、H5TQ4G63EFR 的组合参考。
- `litedram/phy/gw5ddrphy.py`：支持 nphases=2/4；DLL-off 明确使用 CL6/CWL6，包含 byte-lane delay/bitslip 与 burst detection。
- `litedram/core/crossbar.py`：`get_port(data_width=128)` 支持从内部宽端口转换到 128 bit，并可指定 clock_domain。
- `liteeth/phy/gw5rgmii.py`：GW5 RGMII PHY 参考实现。

这些文件的存在证明有可复用实现，不证明当前安装版本已通过本工程的板测。应在仓库依赖锁中保存其准确源码身份；不要只记包名，也不要把 `.venv` 当作长期源代码仓库。平台默认使用 ES part，仍须覆盖为实物器件。

首次评估时源码HEAD为a34315a且工作树为空。20K的`build/refs/current.json`指向v0.11.0 / ed502601d172实板参考包：rv32ima、MMU、无FPU、无C、60/120MHz、8KiB ROM、8KiB L2。该信息用于旧机器对照；138K以AE350硬核配置描述建立独立身份，不选择旧VexRiscv产物作为其CPU输入。

## 需要建立的板级边界

当前以下内容直接绑定 20K：

- `gateware/soc.py` 导入 20K Platform，构造 GW2APLL、GW2DDRPHY，直接写外设引脚与 F10 共用 PHY reset。
- `gateware/ddr_boot.py` 固定 2 个 lane、2 个 DFI phase、每 phase 64 bit，以及固定训练模式的拼接。
- `firmware/bootloader/ddr.c` 固定两条 lane 的训练、寄存器布局、延时与模式寄存器设置。
- `gateware/memory.py`、`shared_l2.py` 固定 128 bit / 16 字节内存行。
- `gateware/memory_map.py` 固定 128 MiB RAM，板级 JSON 与 firmware/build metadata 也保存固定值。
- `scripts/modular/configuration.py` 固定 60/120 MHz、PLL 数和内存行为；`scripts/modular/rtl.py` 的 PnR 资源解析要求 GW2 的 rPLL/DQS 等资源项。

建议引入板级描述与实现接口，负责 platform、CRG、DDR 物理参数、后端、资源/电压、外围 PHY 和安全启动布局。通用 MiniSoC 保留 CPU、地址空间、CSR 组织、共享 L2、DMA 和功能组装。

构建仍使用既有 `scripts/mini.py build/plan`，增加 board/device/ddr-profile 等选择。板型、时钟、PHY 源码、DDR 几何、约束和工具身份必须进入 soc-description/gateware 的构建键、锁文件及复现 recipe，禁止跨板误复用 gateware。软件兼容性按接口合同判定；bootloader 的 DDR 初始化后端有实质差异，必须选择匹配产物，不能靠相同 CSR 地址推定兼容。

## 历史 VexRiscv 对照：GW5DDRPHY + LiteDRAM

1. 使用 50 MHz 输入和 GW5APLL，先生成与当前相同的 sys=60 MHz、DDR CK=120 MHz；采用 GW5 的 DHCE、CLKDIV、DDRDLL/DQS 原语及初始化顺序。
2. 使用实物匹配的 x32 DDR 几何；零售 Hynix 路线参考 H5TQ4G63EFR。低频 DLL-off 的 CL6/CWL6 是待上板验证配置，不是从 400 MHz 示例直接拷贝时序。
3. 将 DDRBoot 的 DFI 数据位宽、lane 数、训练图案、burst 检测和训练结果参数化。GW5 的调节接口使用 CSRStorage/CSR，当前 vendor GW2 PHY 则导出独立调节信号，需有显式适配。
4. 修改 ROM 初始化为四条 byte lane 的窗口扫描和独立结果记录。现有 8 KiB ROM 是否仍够用要在链接后核对。保留 pinned-L2 启动栈并验证 DDR 交接后的写回，避免训练前依赖 DDR。
5. x32 BL8 是 256 bit 物理突发；先在 LiteDRAM crossbar 请求 `get_port(data_width=128)`，使 SharedMemoryController 继续消费 16 字节逻辑行。不能把 raw 256 bit 直接接到现有 L2。
6. 对宽度转换验证上下半行地址、byte mask、背压、单独半行写后读另一半行、连续写读次序和 reset/cancel。不能只靠全宽递增数测试判断正确。

主路线的收益是保留现有控制器、共享 L2 和 DMA 一致性边界；主要风险是 GW5 四 lane 训练与复位时序仍需本工程独立验证。

## 历史 DDR 对照：官方 400 MHz IP

`example/ddr_test/Hynix_400MHz` 是零售版本参考；`Micron_400MHz` 是定制版本参考。Hynix 生成参数为 x32、1:4、BL8、CL6/CWL5、15 行位；Micron 为 14 行位、CL5/CWL5。参数不能混用。

示例为 400 MHz memory_clk 与约 100 MHz 用户域，256 bit wr_data/rd_data、32 bit wr_data_mask、独立 cmd_ready/wr_data_rdy，以及 init_calib_complete。需要一个 native-port-to-Gowin-app 桥，而不是单独替换 LiteDRAM PHY。

桥接应明确地址单位（app 地址以 DQ beat 计，BL8 跨 8 个地址，不能直接使用字节地址或 16 字节行号）、byte mask 极性、命令/数据独立握手、读响应暂存和复位排空。IP 接收写数据不等于外部 DRAM 已物理完成；需要依据 IP 的命令顺序契约维护 CPU/DMA 可观察的写后读、flush、fence 语义。

138K不保留旧板卡CPU/DDR频率作为兼容约束。当前AE350路线以CPU最高800MHz、所选官方DDR接口的DDR3 CK最高400MHz（800MT/s）为规格内目标，独立测试各时钟域的可达上限，再验证组合稳定性；此处早期VexRiscv方案不再决定新平台频率。各profile必须生成实际时钟元数据，并据此配置timer、UART、SD、音频分频和软件延时；跨域路径按真实时钟关系验证CDC。详见[频率上限测试](ae350-migration-stages.md#频率上限测试)。

## 历史 VexRiscv 阶段表（当前执行 AE350 分阶段计划）

| 阶段 | 工作 | 完成标志 |
| --- | --- | --- |
| 0. 固定硬件身份 | 确认 Pro/PCB 修订、FPGA part、DDR 颗粒、Flash、JTAG/UART 接口；保存 20K current 基线 | 板级配置可重建；不混用 ES/量产、Hynix/Micron |
| 1. 最小板级系统 | 选择 138K 平台、GW5 CRG、BSRAM ROM/临时栈、UART、LED；其余功能关闭 | SRAM 下载可运行，UART 115200、按键/软复位重复成功，PnR 无未解释时序违例 |
| 2. 独立 DDR | 先跑匹配装配的官方测试作为对照，再验证 GW5DDRPHY 四 lane 训练 | 每 lane 有可靠窗口；byte/halfword/word、边界、bank/row、高地址无 alias；多次复位与冷启动成功 |
| 3. BIOS 与一致性 | 接入 128 bit 适配端口、原共享 L2、bootloader 与 UART DDR 装载 | BIOS 命令、内存自检、L2 开关/flush、CPU↔DMA 脏数据可见性与取消/背压回归通过 |
| 4. 恢复常用外设 | SD → RGB LCD → PT8211/WS2812/按键 | SD 读写/FatFs、LCD 色位/换帧/无 underrun、音频采样率/声道/静音分别通过 |
| 5. RGMII | GW5 RGMII PHY、RTL8211 MDIO/reset、25 MHz PHY 参考与 TX/RX 时钟、延迟配置 | 先 PIO 双向收发/ARP/ping，再接既有 ring DMA，负载下无丢包或一致性错误 |
| 6. USB 与系统软件 | 单独确定 USB Host 硬件/PHY；恢复 Linux/OpenSBI/U-Boot、应用 | USB 键鼠真实枚举；Linux 内存/设备树正确；Doom 等应用通过现有工作流 |
| 7. 扩容提速及持久化 | 开放更多 RAM、调 sys/DDR/L2；最后设计并执行 Flash 安装 | 固定参数三轮 benchmark、完整 PnR/实板验收、Flash readback 与断电启动分别有证据 |

阶段 1 临时 BSRAM 栈用于独立 bring-up；阶段 3 应明确选择恢复当前 pinned-L2 栈或正式迁移到独立 SRAM，不能让实验栈无意改变启动合同。

LCD 首先用实际连接的面板测试色条。800x480 示例的 top 注释写 10 MHz，不能据此确定真实像素频率；读取 PLL 配置并按面板规格重新核算刷新率、porch 与 DE/HS/VS 接法。内部 RGB565 通过末端色位扩展映射到 RGB666，避免为首版改变 framebuffer ABI。

RGMII 官方例子 `udp_rgmii_send` 只有发送通道，不足以证明 RX、自动协商、管理总线或完整网络栈。原理图 RTL8211 PHY 地址 strap 为 1，RX/TX 延迟电阻与软件 delay 设置要一起核对，避免重复加延迟。

USB 方面，当前 ultra 后端的 UTMI Host 核心有复用价值，但 USB3317 ULPI 初始化、serial mode 和板上供电控制不能保留。可评估外接合适 PHY/Host 硬件，或验证板载 SoftPHY 与现有 Host 的接口及电气支持；配套 Device demo 不足以选择后一方案。BL616 的 Host 模式也不能直接当作 FPGA 的 USB Host 寄存器接口。

Flash 初期只做 JEDEC/读取与布局核对。20K 的 app_offset=2 MiB 不能搬到 138K：bitstream 大小、配置模式、两颗 Flash 分工和 GW5 配置引脚回收方式都不同。本轮没有进行下载、Flash 写入、频率性能测试或板级资格认定。

## 补充：改用 AE350 硬核的路线

用户已确定使用自带AE350、复用构建框架并替换CPU到总线基础设施。前文60/120MHz GW5DDRPHY建议属于保留VexRiscv的历史对照；当前采用AE350平台，首版不保留旧SharedL2。P0主机部分已完成，尚未形成新平台实板结果。

已读取 `example/ae350_customized_demo/src/ae350_customized_demo.v`：示例使用 AE350 定制 SoC wrapper，通过 64 bit DDR AHB 接 `gw_ahb_ddr3_top`，由独立 Gowin DDR IP 校准，完成后释放 CPU reset；另有 UART2、SPI Flash、GPIO 和调试 JTAG。顶层 CORE_CLK 注释为 800 MHz、AHB/APB 为 100 MHz，但这只是示例配置，不是本项目性能测量。DDR 频率注释前后不一致，实际构建必须对照 PLL 和 IP 参数。

当前 LiteX 的 `soc/cores/cpu/gowin_ae350/core.py` 也已有硬核实例、ROM/RAM/外设 AHB 到 Wishbone 桥、16 位 GP_INT、PLIC/PLMT 和 Sv32 的软件描述，以及直接接 LiteDRAM 的可选 memory bridge。因此可保留 LiteX/Migen 组织外围模块，不必全部改用厂商封闭 wrapper。两条入口需要选定一个；官方示例用于先建立硬件运行对照，长期集成可评估 LiteX。

复用边界：

- 应用、BIOS shell/终端编辑、FatFs 和上层文件操作、图形绘制/应用逻辑可较多复用，但需重新编译并替换平台依赖。
- RGB scanout、PT8211、WS2812、SD 控制器等外围逻辑可复用核心；CSR、IRQ、DMA 入口与时钟需要适配。
- VexRiscv generator/CPU RTL、GW2 PHY/训练、旧 CRG、启动栈技巧和针对旧 CPU 的 fence 接法无需保留。
- 现有 shared L2 是可选的 fabric cache 设计，不是 AE350 的必需部件。不能把它原有的“一致性边界”直接套用到 AE350 私有 cache。
- 早期 bootloader、trap/IRQ、timer、linker、OpenSBI 平台、设备树、cache maintenance 与 app image 兼容性需重新核定。

特别是 `firmware/hal/src/usb.c` 的 cache clean/invalidate 回调当前只执行 fence，这不足以表达 AE350 缓存维护。首版 DMA 应采用经过确认的非缓存区域/属性映射，或真正的 cache clean/invalidate API，明确 CPU→DMA 与 DMA→CPU 的缓冲区所有权；普通 fence 不能代替数据缓存写回和失效。

AE350当前阶段顺序为P0固定板卡/硬核/工具输入 → P1最小启动与恢复 → P2 DDR/cache → P3共享访问/DMAC → GPIO、audio/mic、HDMI及SD/BIOS → 可选网络/USB/Linux。详细交付和验收见[分阶段计划](ae350-migration-stages.md)。CPU时钟提升不会按比例提高DDR、AHB或外设带宽，仍需测量。

## 来源文件

- `docs/01_Specification/Tang_MEGA_138K_Pro_Specification_V1.0_en-US.pdf`：早期总体规格。
- `docs/02_Schematic/TANG_MEGA-138K-Pro_4071_Schematics.pdf`：第 7 页时钟/UART/Flash/按键，第 8 页 DDR 拓扑与电压。
- `docs/02_Schematic/TANG_MEGA-138K_Pro-Dock-4071f_Schematics.pdf`：第 3 页外部时钟，第 6 页 BL616，第 13 页 SD，第 14 页 LCD，第 17 页 RTL8211，第 18 页音频。
- `example/ddr_test/readme.md`、Hynix/Micron 的 `.ipc`、`temp/DDR3/gwmc_param.v`、`src/top.v`、PLL/CST/SDC。
- `example/pro_ddr_test`：带 UART 的 DDR 测试及命令/数据接口用法。
- `example/ddr_test/docs/IPUG281-2.2E.pdf`：第 4.4 节命令、地址和数据时序。
- `example/rgb_screen`、`audio/audio_sinewave`、`udp_rgmii_send`、`usb2_soft`：外围接口及其示例覆盖范围。
- `TangPrimer-20K/riscv-mini/gateware`、`firmware/bootloader`、`scripts/modular` 与本地 `.venv` 中上述具体依赖源文件。
