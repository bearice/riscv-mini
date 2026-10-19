# 多平台组织、外设解耦与兼容性设计提案

日期：2026-10-17。状态：P0的Board/Machine描述、硬核配置及构建隔离已落实，见[P0构建底座](ae350-platform.md)；其余分层接口与RTL/BSP重构仍为设计提案。用户已确定复用构建框架、为AE350更换CPU到总线基础设施，选择性复用audio、mic、GPIO及通用视频逻辑；LCD输出迁移暂缓。本文未实施部分不能视为已有能力。

视频范围更新：用户随后明确138K使用HDMI，暂时搁置LCD移植。下文涉及LCD的现状分析仍适用于20K；138K仅复用通用像素/调色板/framebuffer/换帧逻辑，输出层采用TMDS encoder/serializer。建议设备组织为video公共逻辑加rgb_lcd/hdmi输出Adapter，设备及运行时描述区分输出能力与像素模式，不将HDMI绑定为旧RGB LCD寄存器布局。具体阶段见 [AE350分阶段移植与验收计划](ae350-migration-stages.md)。

## 1. 组织原则与现状

保持单仓库和唯一 `mini.py build/plan` 入口。20K/VexRiscv 和 138K/AE350 是两个机器配置，不应复制整个工程，也不应在同一个 MiniSoC 中不断增加 board 判断。

当前具体耦合：

- `gateware/soc.py` 同时选择 CPU、20K Platform、CRG、DDR、地址空间、外围引脚和总线连接。
- `audio.py::Audio` 同时拥有 PT8211 serializer、FIFO、CSR、Wishbone ring reader 和全局 RAM 范围检查。
- `microphone.py::Microphone` 同时拥有 I2S 接收、snapshot 生命周期、FIFO 和 CSR；当前 snapshot 并非 DMA。
- `video.py::RGBLCD` 同时拥有 CSR、调色板、CDC、内存请求/帧生命周期和输出；LCDScan/PalettePixels 已有可复用拆分，但仍有固定面板尺寸和 GW2 BSRAM 推断约束。
- `board_io.py` 固定 LED/按键数量、有效电平与 debounce 默认值；`hal.h` 暴露 Key2–5、特定开关引脚、F10 共用 reset 等板卡细节。
- `contracts.py` 已区分 identity 与 compatibility，但 provider 的 platform 固定为 riscv-mini，consumer 默认保存全 CSR/内存/IRQ 快照及全局时钟/后端行为，缺少机器和设备级需求模型。
- `hardware.py` 假设 CPU 产物包含 Verilog，固件后端直接导入 `gateware.memory_map`；软件构建因此仍依赖某一套硬件实现。
- `source_files()` 的目录遍历只收集 .c/.h/.S/.ld/.py；新平台的 .v/.sv/.json/.cst/.sdc/.ipc/.init 等输入必须显式纳入闭包。本地修改的 LiteX/LiteDRAM 源码不能只用包版本标识。

目录调整必须与依赖改变同时发生。仅移动文件、增加原样转发层或把 SoC 拆成多个仍相互依赖全局常量的文件，不算完成解耦。

## 2. 板卡与机器分开

板卡描述拥有 FPGA part、核心/底板修订、引脚、电压、板载晶振、复位极性、DDR 装配和外接面板/麦克风信息。它不规定 BIOS ecall 或驱动寄存器布局。

机器配置拥有 CPU kind/capabilities、启动链、内存布局、中断架构、时钟树、DDR 后端、设备实例及连接、cache/DMA 规则。一个 Board 可同时承载 VexRiscv 和 AE350 方案；具体机器引用准确的 Board 变体。

`minimal/full` 只作为某个机器的功能预设，不再代表跨机器相同的 feature 组合。请求不支持的功能或不存在的引脚必须报错；禁止默默关闭功能、替换 CPU 或退回另一 DMA 后端。

## 3. 建议目录

```text
gateware/
  boards/                 # 物理资源与装配变体
    primer20k_dock3713/
    mega138k_pro/
  machines/               # 最终组装点，选择下面的实现
    primer20k_vexriscv.py
    mega138k_ae350.py
  cpu/                    # CPU 实例/能力适配，软核和硬核各有实现
  memory/                 # DDR 后端、仲裁、20K shared L2 等
  interconnect/           # 已实际使用的桥、宽度转换、CDC
  devices/
    audio/                # core.py、registers.py、assemble.py
    microphone/
    gpio/
    lcd/
  transport/              # stream reader/writer、ring、DMAC endpoint 连接
firmware/
  hal/include/            # 上层功能接口，不暴露具体板脚/CSR
  bsp/                    # CPU/runtime/cache/IRQ/启动/链接布局适配
    primer20k_vexriscv/
    mega138k_ae350/
  drivers/                # 按设备接口组织，尽量不按板卡复制
  bios/                   # shell、终端、文件服务等
  apps/
interfaces/               # 设备接口与运行时 ABI 描述
scripts/modular/          # 保留调度/store/replay，消费已解析机器描述
```

只创建有实际消费者的目录。共享的音频 core 不必单独发布预编译 RTL 产物；是否成为独立构建目标由真实产物需求决定。逻辑 module 与 store 中的构建组件不是一一对应关系。

## 4. 外设的四个实际分离点

### 功能核心

处理串行协议、采样/像素格式、功能状态和确定性的时序。接受配置/命令和数据流，输出状态/事件；不 import Board、RAM_BASE、Wishbone、DDR 或 CPU CSR，不主动获取平台资源。

协议核心的 Interface 必须写明时钟域、配置生效点、reset、underflow/overflow 和背压语义。音频引脚和 LCD 像素时序不能被上游背压停住：音频空 FIFO 的静音/计数策略、LCD 缺像素的输出/丢帧/重同步策略必须固定。mic 无法暂停外部样本，需定义丢弃还是中止采集及 overflow 记录。

### 控制与寄存器

设备 register model 将 CSR/MMIO 访问转换为核心命令，并固定 W1C、read-pop、快照、计数器、配置提交等语义。同一语义不分别在 AHB 和 Wishbone wrapper 中实现一遍。

20K保留现有LiteX CSR/Wishbone接法。138K直接使用AE350导出的AHB/APB，以原生从设备适配寄存器模型；P1 ROM/SRAM也直接连接AHB，不为复用SoCCore引入Wishbone桥或位宽转换。同一寄存器语义由测试约束，功能核心和DMA路径保持独立。

### 数据搬运

serializer/scanner/receiver 与数据源分开，使用明确的 PCM/像素/采样流。FIFO 在满足时钟、突发和实时性约束的位置，由一次组装明确拥有，避免 core、DMAC 和平台各加一层重复 FIFO。

- 20K Adapter 可继续使用既有 Wishbone reader、buffered native ports、shared L2。
- AE350 Adapter 可使用 DMAC 对可寻址 FIFO 读写，或在确有实时性/描述符需求时使用专用 reader。
- DMA_REQ/ACK 是流量控制握手，不是 PCM/像素数据本身；还需要可访问的数据寄存器/FIFO、方向、阈值、传输宽度、完成和取消规则。
- ring/framebuffer 地址生成器属于相应数据搬运 module；不强行把具有 frame-start/wrap/palette-commit 语义的 LCD 读取约简为普通 memcpy。

### 平台组装

Machine 选择 core、register model、transport 和 Board 资源，完成 IRQ/CDC/时钟连接，并产生软件可读的设备实例描述。20K 旧寄存器布局可以由它自己的 Adapter 保持；新平台不必复制不合适的旧布局。

## 5. 软件接口与 DMA 所有权

HAL 保留功能语义，如播放 PCM、读取样本、提交帧、获取按键。板脚、物理 IRQ 号、私有 CPU CSR、DMA 通道和 cache line 大小由 BSP/driver 使用，不能泄漏给应用。

驱动依赖设备接口 ID 和机器中的实例绑定；同一寄存器接口的驱动可以跨机器重新编译。不同设备接口通过不同 driver Adapter 满足 HAL，不让同一个文件遍布 board 条件编译。

DMA 使用 BSP 提供的 buffer mapping/同步 Interface：CPU 指针不默认等于 DMA 地址；方向、长度、对齐、cache line 独占、可达范围和所有权都要显式表达。由 DMA 可达内存分配器或 mapping 层检查地址转换、连续性和边界。

所有权序列：CPU 填充/准备 → sync-for-device → 提交设备 → 完成或可靠停止/排空 → sync-for-cpu → CPU 使用。接收缓冲区交给设备前也要正确处理可能存在的脏缓存；不能只在完成后盲目 invalidate。禁止 CPU 与 DMA 同时写同一缓存行。

20K 实现通过其已验证的 fence/共享内存规则完成上述操作；AE350 实现使用实际缓存维护和/或已确认的非缓存映射。调用方使用同一语义，不假定两台机器的硬件一致性相同。Linux 驱动沿用内核 DMA API，不能用裸机 HAL 操作替代。

## 6. 六类兼容性分别定义

| 层次 | 谁与谁的约定 | 判定方式 |
| --- | --- | --- |
| 源码接口 | 应用源码 → HAL headers | 功能、类型、错误及所有权语义；通过重新编译验证 |
| 内部硬件 Interface | core → register/transport Adapter | 配置/stream/event/时钟与 reset 规则；组合时类型和参数校验，接口测试 |
| 设备硬件 ABI | 已编译 driver → 设备实例 | 寄存器访问/副作用、数据布局、IRQ、DMA/停止语义及实例地址绑定 |
| 机器执行合同 | BSP/固件 → CPU/机器 | XLEN/endianness/ISA、C ABI、特权模式、trap/IRQ/timer、缓存维护能力、地址/启动布局 |
| 运行时 ABI | payload → BIOS/SBI/OS | 调用约定、函数号、结构体布局、服务能力、地址与资源所有权 |
| 加载/升级格式 | 镜像 → loader/安装工具 | 格式版本、可接受需求、load/entry/range、校验以及 Flash 布局 |

内部 stream Interface 是 module 接口，不等于已安装软件的 ABI。两块板都支持 audio 也不等于它们的驱动或 app.img 二进制兼容。

### Device ABI 与实例绑定分离

每类设备定义稳定的接口 ID，例如 `mini.audio.fifo` / `mini.lcd.scanout`，具有独立 major、受约束的 minor 和可选能力。名称是提案例子，首次落地时统一定名。寄存器描述包含 offset、access width、端序、位域、reset、W1C/read side effects、命令完成/IRQ clear、stop/drain 和 FIFO 数据格式。

offset 相同但 pop/clear/完成语义变化属于 ABI 变化。major 变化默认不兼容；minor 增量必须保证旧 reset/default/必需行为不变。版本号是声明，仍需 schema diff 和行为测试；不能凭 SemVer 自动证明兼容。

实例描述另含 base、IRQ controller/source/trigger、clock/timebase、DMA reachability/alignment、sample/panel 参数等。地址变更不必给设备类型升 major，但会使旧固定 MMIO driver 二进制不兼容；能读取实例描述的驱动可在受支持映射规则下继续使用。

### 机器合同只约束消费者实际使用的环境

CPU kind 的 artifact 身份精确锁定；执行兼容则检查所需 ISA/ABI/特权/CSR，而非仅比较 CPU 名字。ISA 扩展可用还需启动和上下文管理支持；不能仅靠 ISA 超集允许硬浮点 payload。

不全局要求所有 consumer 的 sys_hz、L2 size、DDR burst 相等。只有编译进固定时钟计数的 driver 才要求该频率相等；若使用描述中的 timer_hz，则检查该接口和有效范围。内部 DDR burst 从 16B 改到 32B，不应影响仅调用 BIOS 音频服务的应用 ABI。

138K的CPU/DDR按新平台规格测试上限，不继承20K的运行频率。Machine时钟描述须区分core、数据内存总线、AHB/APB、DDR CK、DDR用户域及timer/外设源时钟；AE350的AHB/APB同步1:1属于平台合法性约束。实际PLL/分频/DDR timing进入构建身份，驱动使用实际源频率或声明固定频率依赖。扫频profile分别保存证据，不能把规格最高值或单域通过值直接发布为完整系统稳定运行值；测试门槛见[频率上限测试](ae350-migration-stages.md#频率上限测试)。

机器特定 bootloader/BSP 对 trap、cache/IRQ 等提出严格要求；通用应用不直接依赖机器合同细节。未声明或未知的必需语义一律拒绝，不能根据“都是 RV32”推定兼容。

### HAL 源兼容与 payload 二进制兼容

HAL 当前静态链接，保持 C 源接口意味着可重编，不保证已有 ELF 可以换平台。运行时继续保留 BIOS ecall v1 时，函数号、结构布局、错误码和资源所有权都须满足；未来变更时独立演进运行时 ABI，不与镜像格式宏共用版本。

旧 payload 有固定 load/limit 和 ISA 等隐含条件。跨平台默认重新编译；要承诺原二进制运行，必须补齐 payload 的 ISA/C ABI/特权、load/entry、required runtime services 与内存需求声明，并在 AE350 上验证。DMA/cache 维护最好由 BIOS/HAL 隐藏，但若旧 ABI 允许 payload 持续修改活动 framebuffer/ring，这个所有权语义必须被新实现满足，或明确建立新 ABI；不能静默收紧。

## 7. 身份、requires 和验证证据

三者始终独立：build key 决定是否重建，artifact digest 决定是否同一产物，requires/provides 决定是否可以组合；测试证据只说明哪些具体组合已经验证。

消费者需求由 driver/BSP/runtime 明确声明，再按实际构建所选模块求并集。不要靠源码 grep 自动推断最小需求；声明不完整时保守扩大，而不是省略已知依赖。HAL 库编入全部功能时可保守依赖全部对应设备，不虚称已经达到每个函数的最小依赖。

requires 至少支持：目标执行合同、所需设备类型/major/能力/参数约束、固定或可发现实例绑定、运行时服务与内存/加载范围。optional 能力缺失仅在消费者确有降级实现时可接受。现有 capability 位号和 runtime feature 位号属于不同命名空间，冻结已发布值，禁止重排或混用。

硬件实例图是唯一解析结果：由它导出 CSR/实例描述、C headers、linker 输入、BSP 配置、设备树和 compat manifest，防止分别维护 JSON、全局 Python 常量和 C 宏漂移。接口 schema 是语义来源，elaboration 输出用于核对实际布局；不得让双向生成形成循环依赖。

构建顺序：展开 Board/Machine/模块配置 → 锁 CPU/IP/工具/源文件 → elaboration 产生无具体 ROM 内容的 SoC/软件描述 → 构建 BSP/HAL/bootloader → 将准确 ROM 内容与硬件锁组合生成 gateware → BIOS/应用 → system bundle。明确区分 ROM 嵌入、独立 Flash、ILM 初始化等启动方式，仅真实嵌入才建立内容依赖。

软核 CPU 组件包含 RTL+生成闭包；硬核 CPU 组件包含 part/macro/IP 配置、集成源码与工具身份，不伪造一份 CPU Verilog。RTL、约束、init 数据、IP 配置/加密 HDL、实际依赖源码和生成器配置都进入闭包；禁止无身份依赖工作区外绝对路径。

保留精确边：SoC 消费 CPU/IP、gateware 消费硬件/嵌入 ROM、BIOS 消费静态 HAL 库。运行兼容边则按 needs/provides 检查。兼容通过不意味着旧产物 build key 命中。

`current`/`baseline` 与实验/板测选择需按机器隔离；未显式选择机器的旧命令在迁移期只指原 20K，不允许猜测最近构建的平台。

## 8. 旧格式与新平台

20K 已安装 v1 loader 继续使用原 tag/镜像规则；不因主机 v2 contract 通过而放宽旧 loader。新机器初期采用精确绑定的 boot bundle，之后再实现能理解分层需求的新加载格式。manifest schema 演进与 Device ABI major、runtime ABI、镜像格式版本分别管理。

旧整机 contract 只导入成 legacy 精确锁，不自动伪造为新的细粒度设备契约。`abi_tag()` 中固定 60MHz、VexRiscv、load/Flash 布局继续局限于 legacy 20K，不作为 AE350 通用 packer。

## 9. 迁移与验收

1. 固定现有 20K 代码/产物/板测参考；先定义 Board/Machine 与新 contract 的独立 schema，保留 legacy loader 行为。
2. 调整输入闭包和构建 CPU kind，建立机器独立 refs、BSP/驱动选择及元数据生成；所有入口仍在 mini。
3. 从 GPIO 和 mic 开始提取功能 core/register model，在原 20K Adapter 下证明外部行为不变；随后拆 audio，最后拆 LCD 的控制/扫描/reader/CDC。
4. AE350最小系统单独跑通，接入共享GPIO/mic/audio core；先验证单设备FIFO/DMAC，再接HDMI和上层软件。
5. 逐一接入 payload/OS 的完整 requires 检查；实际完成前保留当前工具的能力限制说明。

验收矩阵至少包含：

| 场景 | 预期 |
| --- | --- |
| 改 AE350 PLL/CST/IP 参数 | 138K 相关硬件重建，20K 产物不被误命中或无故失效 |
| 改共用 audio core | 两机器中启用该 core 的 gateware 重建 |
| 改 audio 数据搬运 Adapter，设备 ABI 不变 | gateware 重建；driver 是否兼容按可观察行为/实例约束判定 |
| 新增 reset-disabled 可选寄存器 | 老 driver 可兼容；新 driver 要求新能力时老硬件拒绝 |
| FIFO pop、IRQ clear 或停止/排空语义改变 | 即使地址相同也拒绝旧必需语义 |
| 更换 MMIO base/IRQ | 固定绑定二进制拒绝；可发现绑定且支持该 IRQ/寄存器模型的驱动可继续用 |
| 调 sys_hz 或内部 DDR burst | 仅真实依赖它的 consumer 受影响；固定计数 driver 不可被误放行 |
| v1 image + 新主机 compat | 仍必须通过已安装 v1 loader 的原规则 |
| AE350 DMA 缓冲区重复使用/取消/reset | 缓存与所有权测试通过，完成中断不能早于可安全回收条件 |
| mic overflow、audio starvation、LCD underflow/换帧 | 各自协议时序不被背压冻结，错误/恢复语义可观察且有测试 |

每个 core 在它的功能 Interface 测试；每个 Adapter 用真实总线/CDC/背压测试；完整机器另做 PnR 和实板验证。软件调用接口不变不等于这些硬件验证可以跳过。初期不建全排列配置矩阵，只覆盖两台实际机器及每个新增 Adapter 的关键行为。

## 10. 当前未确定项

AE350 的最终总线组织、DDR 后端、启动介质、哪些外围接内置 DMAC，以及新加载格式的具体 wire layout 尚未确定。本提案定义它们的所有权和检查规则，不预先承诺某种 IP 或二进制跨平台运行。
