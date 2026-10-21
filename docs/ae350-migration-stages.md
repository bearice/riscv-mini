# AE350 后续平台路线

当前平台以厂商 ITCM 启动、硬核 ILM/DLM 和零基址 DDR 为基础。旧 P0/P1 过程说明、fabric ROM/SRAM 监视器和临时调试入口已退役；历史实现见 snapshot commit。当前交付依据为[内存验证合同](ae350-memory-validation.md)、[平台操作](ae350-platform.md)与[最终验收报告](../reports/ae350-p2.md)。P3 的板测记录写在该节末尾。P4 的实现和仍开着的板级门禁见 [板级 I/O](ae350-io.md)。

### P3：互连、硬核DMAC与缓存所有权（当前运行点完成）

最终接口与使用限制见 [DMA 服务](ae350-dma.md)，验收说明见 [DMA 验收](../reports/ae350-dma.md)。原验收命令 `board p3-suite` 整理为持续复用的 `board dma-suite`；历史回执路径不改写。频率扫描暂停，PLIC 到 CPU 中断服务不签收。P4 可开始；正式 DMA 外设接入前须闭合忙通道保护和硬件错误传播。

地址、CSR、DMA数据端点、共享buffer与兼容合同采用[AE350统一地址规划](ae350-address-map.md)；沿用硬核DMAC原址，外设控制与数据口分离，不继承20K的CSR编号。

交付：扩展MMIO/IRQ路径、DMA buffer映射/同步接口、测试FIFO/loopback外设、已冻结的fabric DDR访问拓扑。

通过：

- DMAC做至少10000次混合传输：不同长度/对齐/边界、固定/递增地址、链表/循环、双通道竞争；支持组合逐字节匹配，不支持配置明确拒绝，guard区无破坏。
- 在cache开启时验证CPU→DMA与DMA→CPU，交还前后的clean/invalidate与对齐/所有权规则正确；当前新值在clean前可见，签收旧行和范围失效，不宣称脏行写回通过。
- 外设FIFO真实接DMA_REQ/ACK，在背压、空/满、取消、超时、软复位时不重复/丢失已承诺数据；停止后未完成事务排空，内存方可复用。
- 若fabric reader使用独立DDR端口，CPU与其并发读写通过同一测试；若只验证了DMAC，必须把专用reader标为尚未支持。
- 当前运行点为 80/100/400 MHz；频率扫描按用户决定暂停，高频组合不在本阶段完成声明中。
- 错误的机器/设备major、MMIO/IRQ绑定、DMA可达范围和required capability有拒绝测试；兼容的可选新增寄存器有接受测试。

P3是进入任何正式DMA外设的门槛，不因GPIO或CPU memcpy可用就跳过。

位流 `d6a32f5add1b` 的原始验收回执为 `build/runs/20261020T080229355702Z-board-ae350-p3-suite`，只编程 FPGA SRAM。最终结果和未验收范围统一见 [DMA 验收](../reports/ae350-dma.md)，不以通用上传成功替代套件通过。

### P4：共享外设模块与基本 BSP（约定范围完成）

最终合同见[板级 I/O](ae350-io.md)，身份、回执和性能见[验收报告](../reports/performance/ae350-board-io.md)。当前候选 `44405c059cf8` 已通过共享核心与 20K Adapter 回归、PLIC 到 CPU ISR、硬 GPIO 输入、板级 LED 和事件页、WS2812 数字合同、DMA/CPU DDR/IRQ 并发、完整 STA 及三轮性能对照。

普通输出使用板级逻辑寄存器，当前硬 GPIO IOPORT 未启用，不称 LED 经硬 GPIO 输出。软件和 RTL 的映射由板级描述生成。灯、按键和颜色保留用户此前的物理确认记录，与本次自动数字回执分开。诊断载荷、结果合同及 CLI 使用功能命名，后续持续复用。

风扇调速、停转和硬件修改按用户要求暂缓，不阻塞约定范围的 P4 交付。实验栅极接口保留，默认持续供电，示例不再修改它；不签为可用调速功能。仅写 SRAM，不改 Flash/current。下一阶段实时 DMA 外设仍须先闭合忙通道保护和硬件错误传播。

### P5：audio与mic，验证第一条实时DMA链

交付：复用PT8211 TX、I2S RX核心；CSR与数据搬运分离。audio先FIFO/PIO模式对照，再接DMAC；mic先snapshot，再流式采集。J28/PMOD0/SSPI只能由一个已选功能拥有。

通过：

- PT8211 WS左右声道、数据边沿、PCM符号和48kHz采样率正确；采样时钟误差目标<=0.1%，无法达到时明确报告实际值并修改规格，不能冒称48kHz。
- 连续播放30分钟，稳态underrun/error=0；主动断供时静音/计数符合接口，停止/重启100次无重复旧样本或未清DMA。
- mic使用已确认外接模块，单路样本计数/符号/声道正确，刺激能产生可观察响应；snapshot与stream规则分别验证，主动满FIFO能报告overflow并恢复。
- 音频数字时序通过与物理听音分开记录；未接麦克风或没有听音条件时保留对应验收项为待验证，不影响其他已过阶段。

### P6：HDMI视频与图形控制台

交付：单路TX、视频时序/TMDS encoder/serializer、scan/pixel/FIFO/reader/CSR拆分，DDR framebuffer、RGB565/INDEX8、换帧/调色板/回卷按能力启用；恢复BIOS终端显示。640x480/60是建议首模式，720p/60后续单独通过；二者不是用户已指定的分辨率要求。

通过：

- P6a先不依赖DDR，用测试图确认单路TMDS差分对/通道、pixel与serializer时钟及相位/reset、同步极性和完整边界；显示器连续锁定30分钟，无黑屏/闪点。支持HPD检测时验证拔插恢复；没有实现HPD时明确记录重新锁定/复位流程。
- 模拟图案的完整framebuffer与输出像素顺序一致；上下边界/stride/双缓冲/帧首提交不混帧；支持的YWRAP/INDEX8分别测试。
- P6b接入DDR后，目标模式连续30分钟，在CPU内存压力+audio+mic负载下video稳态underflow/address_error=0，audio无underrun；记录最长DDR服务空窗与FIFO最低水位。
- 独立可用读带宽先达到目标有效像素带宽的1.5倍作为准入参考；最终以组合负载下不饥饿为准。RGB565 640x480/60参考55.296MB/s，720p/60参考165.888MB/s；低分辨率通过不代表720p已通过。
- 注入一次长时DDR阻塞能按定义丢帧/恢复，无死锁；物理显示色彩、边界、撕裂/闪烁另做目视验收。TMDS视频通过不等于HDMI音频、EDID协商或CEC通过，这些能力需独立声明。

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
