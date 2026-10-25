# AE350 后续平台路线

当前平台以厂商 ITCM 启动、硬核 ILM/DLM 和零基址 DDR 为基础。旧 P0/P1 过程说明、fabric ROM/SRAM 监视器和临时调试入口已退役；历史实现见 snapshot commit。当前交付依据为[内存验证合同](memory-validation.md)、[平台操作](platform.md)与[最终验收报告](../../../reports/ae350-p2.md)。P3 的板测记录写在该节末尾。P4 的实现和仍开着的板级门禁见 [板级 I/O](io.md)。

### P3：互连、硬核DMAC与缓存所有权（当前运行点完成）

最终接口与使用限制见 [DMA 服务](dma.md)，验收说明见 [DMA 验收](../../../reports/ae350-dma.md)。原验收命令 `board p3-suite` 整理为持续复用的 `board dma-suite`；历史回执路径不改写。频率扫描暂停，PLIC 到 CPU 中断服务不签收。P4 可开始；正式 DMA 外设接入前须闭合忙通道保护和硬件错误传播。

地址、CSR、DMA数据端点、共享buffer与兼容合同采用[AE350统一地址规划](address-map.md)；沿用硬核DMAC原址，外设控制与数据口分离，不继承20K的CSR编号。

交付：扩展MMIO/IRQ路径、DMA buffer映射/同步接口、测试FIFO/loopback外设、已冻结的fabric DDR访问拓扑。

通过：

- DMAC做至少10000次混合传输：不同长度/对齐/边界、固定/递增地址、链表/循环、双通道竞争；支持组合逐字节匹配，不支持配置明确拒绝，guard区无破坏。
- 在cache开启时验证CPU→DMA与DMA→CPU，交还前后的clean/invalidate与对齐/所有权规则正确；新值在clean前可见，不宣称脏行写回通过。同步前旧行是否驻留保留为每次实测观察，新版 suite 强制同步后数值和相邻 guard 正确，不要求缓存驻留。
- 外设FIFO真实接DMA_REQ/ACK，在背压、空/满、取消、超时、软复位时不重复/丢失已承诺数据；停止后未完成事务排空，内存方可复用。
- 若fabric reader使用独立DDR端口，CPU与其并发读写通过同一测试；若只验证了DMAC，必须把专用reader标为尚未支持。
- 当前运行点为 80/100/400 MHz；频率扫描按用户决定暂停，高频组合不在本阶段完成声明中。
- 错误的机器/设备major、MMIO/IRQ绑定、DMA可达范围和required capability有拒绝测试；兼容的可选新增寄存器有接受测试。

P3是进入任何正式DMA外设的门槛，不因GPIO或CPU memcpy可用就跳过。

位流 `d6a32f5add1b` 的原始验收回执为 `build/runs/20261020T080229355702Z-board-ae350-p3-suite`，只编程 FPGA SRAM。最终结果和未验收范围统一见 [DMA 验收](../../../reports/ae350-dma.md)，不以通用上传成功替代套件通过。

### P4：共享外设模块与基本 BSP（约定范围完成）

最终合同见[板级 I/O](io.md)，身份、回执和性能见[验收报告](../../../reports/performance/ae350-board-io.md)。当前候选 `44405c059cf8` 已通过共享核心与 20K Adapter 回归、PLIC 到 CPU ISR、硬 GPIO 输入、板级 LED 和事件页、WS2812 数字合同、DMA/CPU DDR/IRQ 并发、完整 STA 及三轮性能对照。

普通输出使用板级逻辑寄存器，当前硬 GPIO IOPORT 未启用，不称 LED 经硬 GPIO 输出。软件和 RTL 的映射由板级描述生成。灯、按键和颜色保留用户此前的物理确认记录，与本次自动数字回执分开。诊断载荷、结果合同及 CLI 使用功能命名，后续持续复用。

风扇调速、停转和硬件修改按用户要求暂缓，不阻塞约定范围的 P4 交付。实验栅极接口保留，默认持续供电，示例不再修改它；不签为可用调速功能。仅写 SRAM，不改 Flash/current。下一阶段实时 DMA 外设仍须先闭合忙通道保护和硬件错误传播。

### P5：audio与mic，验证第一条实时DMA链

当前实现、BSP 和操作入口见[音频与麦克风](ae350-audio.md)，候选身份、数字验收与物理观察分别记录在[音频验收记录](../../../reports/ae350-audio.md)。PMOD0 已按用户线序绑定单只麦克风。忙通道重提保护及真实 AHB ERROR 传播已在此阶段补齐；REQ[2]/[3] 实测对应 DMAC selector 9/10，不能按线号配置 selector。候选 `b527a0ae09e5` 已通过 30 分钟 TX/RX 双 DMA 与 CPU DDR 并发，underrun/overflow/符号/guard 错误均为 0，100 次 PIO 与 100 次在途 DMA 重启通过；实测帧率约 47,999.77 Hz。本机声卡模拟录音按用户要求暂时跳过，受控声学响应仍为未确认；数字实时链路通过，物理验收不计为全部通过。

交付：复用PT8211 TX、I2S RX核心；CSR与数据搬运分离。audio先FIFO/PIO模式对照，再接DMAC；mic先snapshot，再流式采集。J28/PMOD0/SSPI只能由一个已选功能拥有。

通过：

- PT8211 WS左右声道、数据边沿、PCM符号和48kHz采样率正确；采样时钟误差目标<=0.1%，无法达到时明确报告实际值并修改规格，不能冒称48kHz。
- 连续播放30分钟，稳态underrun/error=0；主动断供时静音/计数符合接口，停止/重启100次无重复旧样本或未清DMA。
- mic使用已确认外接模块，单路样本计数/符号/声道正确，刺激能产生可观察响应；snapshot与stream规则分别验证，主动满FIFO能报告overflow并恢复。
- 音频数字时序通过与物理听音分开记录；未接麦克风或没有听音条件时保留对应验收项为待验证，不影响其他已过阶段。

### P6：HDMI视频与图形控制台

目标仅针对 Tang Mega 138K Pro / AE350；20K 的 480×272 LCD 继续使用原实现。用户已将 HDMI TX 接入本机 USB 采集卡，P6 用它作为视频接收端和可重复抓帧的验收设备。

交付：一条 HDMI 连接器输出；DVI兼容 TMDS 视频；时序、encoder/serializer、像素流、FIFO/CDC、DDR reader 与 CSR 分层；先交 RGB565 双缓冲和 BIOS TTY，再按独立能力门禁加入 INDEX8/调色板、回卷及更高分辨率。首版实际模式为640×480、25MHz像素时钟、59.5238Hz源帧率；720p60 是后续独立目标，尚非用户指定要求。scanout reader 与 CPU/DMAC 经事务轮转共用 DDR UI，跨域 FIFO 和带 epoch 的像素流支持连续扫描。

2026-10-23 状态：P6a 测试图30分钟、P6b 双缓冲100次翻页/100次停启、P6c BIOS VT/共享字体TTY采集、P6d audio/mic/CPU/video并发1810秒均通过。完整PnR setup/hold为0/0，最小脉宽裕量+0.037ns。逐帧USB图样检查、源raster计数与USB时间戳分别记录；UVC不独立暴露HDMI输入时序。详见[实现说明](video.md)与[验收报告](../../../reports/ae350-video.md)。

实施顺序：

1. **P6a：无 DDR 的发射链路。** 冻结实际使用的一个 HDMI 口、TMDS 差分管脚与电气约束；核对板卡实物/版本、管脚 bank 和 VCCIO。设计 AE350 已用 PLL 与视频像素/5×serializer 时钟的组合，明确 lock、相位和复位释放关系；复用经审阅的 SVO/TMDS 核心和测试图，不照搬其 PLL/CST/SDC。默认先不实现 HDMI 音频、DDC/EDID、CEC 或 HPD。用色条、网格、边缘标记等内建图样驱动 TMDS，不访问 DDR。
2. **P6b：DDR RGB565 scanout。** 确定专用 scanout reader 如何进入已验证 DDR 通路，采用有界突发与 FIFO 跨入像素域；定义帧起止、stride、对齐、地址范围、back/front 所有权、vblank 原子换帧、underflow 与 address_error 行为。buffer 从经验证可供外设使用的 DDR 池分配；CPU 写完后按 AE350 非一致 DMA 规则 clean，再提交给 reader。先由诊断载荷写已知帧图案并读回 CSR 状态。
3. **P6c：BIOS 与应用接口。** 为 AE350 定义自己的 video 设备能力、RGB565 framebuffer、present/stop/status HAL；适配 BIOS TTY、清屏/滚屏/ANSI 输出和基础图形呈现。不要沿用 20K LCD CSR 地址或把 480×272 面板几何硬编码成 HDMI 合同。RGB565 稳定后再决定是否启用 INDEX8/调色板、YWRAP。
4. **P6d：组合验收。** HDMI scanout、CPU DDR 压力和 P5 audio/mic 同时运行；记录 video FIFO 水位、最长 DDR 服务空窗、frames、underflow/address_error、audio underrun/overflow，以及采集端的输入模式和丢帧统计。

通过：

- P6a 用采集卡确认锁定到约定的输入尺寸/帧率，抓取色条、RGB 顺序、方向和四边界与预期一致；持续采集30分钟无失锁、黑帧或图样异常。先查询采集卡可接收模式；若不接受640×480p60，记录其报告的模式并选两端共同支持的模式，不能把未验证的640×480记为通过。
- 主机端优先选无压缩 UVC 格式抓帧；若设备只给压缩格式，则按容差检查图形位置/色块并将压缩误差与 FPGA 输出错误分开记录。采集卡验证 TMDS 接收后的图像，不代替示波器对电气眼图/jitter 的测量。
- DDR 帧图对比检查 RGB565 字节/通道顺序、像素方向、首末行列、stride、合法/非法地址、完整帧长度和换帧边界。双缓冲重复提交至少100次，不出现半帧切换/撕裂；故意制造 FIFO starvation 后按定义输出并在下一帧恢复，无死锁。
- RGB565 640×480/60 有效像素流量为36.864 MB/s，独立 reader 带宽达到55.296 MB/s作为准入参考；最终仍以组合压力下无饥饿为准。720p/60 RGB565 分别为110.592 MB/s与165.888 MB/s，另开验收，不继承640×480结论。
- 组合负载连续30分钟，video 稳态 underflow/address_error=0、P5 audio underrun/mic overflow=0；采集端报告无信号中断和异常帧。完整 PnR 的 setup/hold/minimum pulse 通过，新增时钟、CDC、输出约束和实际资源占用有审查结果。
- BIOS TTY 能在 HDMI 上清屏、滚屏、处理约定的 ANSI 序列并恢复文字画面；图形应用能写非活动帧、present 后显示完整帧。HPD 未实现时记录需要重新配置/复位才能恢复；HDMI 音频、DDC/EDID、CEC 不随视频链路通过自动视为完成。

### P7：SD、文件系统与BIOS运行环境

交付：原始SD读写、FatFs、文件命令、BIOS服务和选定应用；软件按AE350 BSP重新编译，不默认沿用旧payload二进制。

通过：

- 先只读已知扇区与主机参考CRC一致，再仅对专用测试文件做1MiB/16MiB/64MiB写读校验；不默认写原始盘或破坏用户文件。
- 对齐/未对齐调用、跨块读写、超时、取消/卸载与DMA可达性正确；单bit/4bit及频率按已实现范围登记。
- BIOS终端编辑、文件操作、音频/图形调用通过；runtime ABI服务/结构/内存所有权检查通过，缺能力的应用明确拒绝。
- audio+HDMI video+SD连续30分钟，文件CRC正确、实时外围错误0；Doom/GW-BASIC等至少选一个形成可重复功能验收，性能另记。

### P8：网络、USB与Linux分支

这三项分别出具结果，不把全部捆绑为一个不可定位的开关。

- 网络：RTL8211 MDIO ID/协商/时钟/delay正确；PIO双向ARP/ping/UDP通过后接DMA或ring；包长/随机内容/环回收/断链重连通过，30分钟负载无内存损坏。记录吞吐/CPU占用/丢包，首版不要求线速千兆。
- USB：先单独验证Device连接以确认SoftPHY，再决定Host硬件与PHY/供电方案。Device通过不计键鼠Host通过；Host需真实键鼠枚举/输入/断开重连，Hub/LS/HS按实际目标分开。板载BL616能调试不计USB Host完成。
- Linux：沿用独立BSP/设备树/OpenSBI平台，先UART+timer+PLIC+Sv32+initramfs启动并有shell，再逐个启用fb/MMC/网络/audio等。每个驱动验证功能和内核DMA API；用户态内存/文件压力30分钟无oops。新硬件F/D/C在工具链和上下文保存测试后分别开启。

网络/USB未完成不妨碍P7成为裸机首个功能版本；Linux内存启动分支可在P3后开始，但全外设验收依赖相应外围阶段。

### P9：完整组合、性能与持久化

交付：冻结机器配置、准确组件组合、最终bitstream/软件、验收报告、自包含复现包；Flash布局和恢复方案单独产物。

通过：

- 完整组合PnR setup/hold=0，审查时钟/CDC/约束覆盖；所有相关功能通过，固定配置混合负载60分钟无不可解释错误。
- 完成频率上限报告：对照规格上限、各域最高通过点、组合最高稳定点与最终运行点，列明失败边界和环境范围；旧20K频率不构成138K验收限制。
- CPU/cache/memory/io和实际应用各三轮，记录中位数、工具/频率/ISA/显示模式，不直接混比20K不同配置；性能提升不是功能正确性的替代证明。
- release/export/verify与独立路径replay通过；硬核/IP/constraint参数变更会失效正确构建键，软件与机器要求不符会拒绝；跨机器refs不串用。
- 只有用户明确授权后才执行Flash写入。写前核对两颗Flash用途/容量/配置模式/实际bitstream尺寸和partition；写后readback与镜像完全匹配。
- 获授权持久化后核对准确启动身份和短功能自检。用户另行明确要求断电冷启动验收时，执行10次Flash独立启动；未执行物理断电测试时只记录实际完成的复位/回读/启动检查。

## 依赖

当前内存 baseline → P3 共享访问/DMAC → GPIO、audio/mic、HDMI、SD/应用。Linux、网络、USB 按各自依赖推进。外设数据通路和 CSR 控制分离；首版视频使用 HDMI，不恢复 LCD 规划。
