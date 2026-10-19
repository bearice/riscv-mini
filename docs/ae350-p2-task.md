# AE350 P2 任务书：DDR 与 CPU cache 正确性

日期：2026-10-18。用途：可直接交给另一名 agent 执行的实现与验收合同。本文为待执行任务，不代表任何 P2 检查已经通过。

## 1. 任务目标与起点

从 `872bc1c981b1486dc696cc06feb5af77687213f9` 开始，在 Tang Mega 138K Pro 上接入匹配实物的 DDR3，完成容量、数据完整性、CPU cache、DDR 执行与固定400MHz CK交付点验证。形成可复现的 P2 候选及准确验收记录，为 P3 的共享访问和 DMAC 提供可信内存基础。

仓库为 `C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini`。`C:\Users\bearice\Workspace\TangPrimer-138K\docs` 和 `example` 是只读板卡参考；项目实现、任务文档和报告写在 riscv-mini 内。

已验证的 P1 基础：原生 AE350_SOC + 8KiB ROM/8KiB SRAM、硬 UART2、独立 CPU JTAG、ROM 完整读回、JTAG 入口重启、ebreak 及 SRAM 恢复。基线位流身份和原始证据见 [P1 报告](../reports/ae350-p1.md)；快照为 `build/archives/ae350-p1-872bc1c981b1`。

**P1 没有整体完成。** 自主启动首轮横幅捕获、重复硬件复位、timer/PLIC/IRQ、实际 CPU 频率和上限压力仍待确认。允许基于现有 JTAG 辅助启动推进 P2；仅补齐会直接阻塞 DDR 测试的前置项，报告分别列出 P1 余项和 P2 结果。压力时长使用主机单调时钟或已独立验证的参考源，不依赖尚未验收的目标 timer。

## 最新决策：单一 DDR 频点与零基址 DDR（2026-10-18）

完整交接与最新实施顺序见[下一阶段实现任务](ae350-next-stage-agent-task.md)。启动主线改为参考20K ROM loader：自主初始化/DDR检查/短POST后通过正常UART加载并执行AE350 payload；JTAG只作可选调试恢复，Flash/XIP实现后置，不作为本轮UART启动闭环前提。

后续CSR、硬核DMAC、AHB数据端点及DMA buffer的完整安排见[统一地址规划](ae350-address-map.md)，作为P3实施合同草案。

400MHz CK 是唯一拟交付及强制验收的 DDR 频点。100/200/300MHz 只保留历史诊断证据，不再补测或要求通过。400MHz 的校准、完整性、cache、执行、压力及真实计时仍须验收；CPU/总线实际频率另行核实。

下一版静态物理布局（待实现）：

| 区域 | 地址范围（含端点） | 初始容量 |
| --- | --- | --- |
| DDR | 0x00000000–0x3fffffff | 预期 1GiB，实际可用容量待完整验证 |
| fabric SRAM | 0x40000000–0x40001fff | 8KiB |
| fabric ROM | 0x80000000–0x80001fff | 8KiB |

ROM 保持已验证的启动入口和原生窗口，不要求紧接 SRAM。这是布局选择，并非复位向量不可改：MUG1029表16-12定义SMU HART0_RESET_VECTOR（0xF0100050）为R/W，复位值0x80000000；当前配置的可写性及各复位域行为仍待实测。DDR CPU 字节地址直接等于 DRAM 偏移，去掉减 0x2000 的转换、低地址 SRAM 覆盖及运行时重映射。SRAM 仍为 fabric SRAM，不与硬核 ILM/DLM 混同。

同步修改机器描述、译码、链接脚本、启动栈/data/bss、JTAG mailbox/结果区、载荷、runner、cache/PMA/PMP 属性和生成元数据。集中分配所有保留区，确保 mailbox、结果、代码、栈互不重叠。DDR 未校准时仍用 ROM+高地址 SRAM+JTAG 诊断。旧 P1 恢复候选保持其原布局。

新布局须重新验证启动、DDR 零地址/末尾/越界、SRAM/DDR 交错及 400MHz 的 P2 功能；不能直接继承旧布局的验收结论。本轮仅更新任务决策，尚未改 RTL 或上板。

## 2. 范围与约束

| 本任务必须完成 | 本任务不推进 |
| --- | --- |
| DDR IP/PHY 配置、CPU 数据路径、时钟/复位/校准、容量验证 | 外设 DMA、fabric 多主共享仲裁及其性能；归 P3 |
| bootstrap 与运行期内存布局、DDR 程序装载/执行 | audio、mic、HDMI、LCD、SD、网络、USB、Linux |
| cache 维护语义、整数/atomic 最小用例、内存压力与频率表征 | SharedL2、为接入 SoCCore 而引入 Wishbone |
| 统一构建/板测入口、证据、恢复与文档 | Flash 写入、固件持久安装、未经明确授权的断电冷启动 |

- 故障定位优先用 JTAG。不得恢复 P1DBG、额外 UART 调试发送器或 TX 接管逻辑；正常控制台及测试结果输出可以保留。
- 不占用摄像头接口；保留 J23 CPU JTAG 与既有 UART 接线。辅助 UART 尚未投入使用，不能作为任务前提。
- 不修改 20K current/baseline，不把 138K 产物交给 20K 后端；不修改参考目录或覆盖旧证据。
- 共享实板同一时间只允许一名执行者编程/调试。并行工作须先约定板卡占用，测试完成恢复至明确记录的可交互状态。
- 不得通过扩大 false path、降低既定目标或少测高地址来换取“通过”。失败应保存并定位。

## 3. 已知硬件与必须核对的参数

实物照片识别为两颗 SK hynix H5TQ4G63EFR-RDC，参考几何为 x32、15 行位、10 列位、8 banks，总计预期 1GiB。此容量是待验证值，不能提前写入已发布可用 RAM。

优先评估 `example/ae350_customized_demo/src/ddr3_memory/gw_ahb_ddr3.v` 与配套 DDR IP；对照 `example/pro_ddr_test`，`example/ddr_test/Micron_400MHz` 只作控制器参考。必须核对完整器件型号、时序、数据宽度、rank/CS、地址映射、DQS/DM、IO 标准与板脚。IPC 中 ROW_ADDR/COL_ADDR 等字段是生成器编码，须确认其枚举含义，不能直接当作行列位数。

优先保持现有 CPU 原生数据 AHB 路径，说明桥接、缓冲、背压和跨域的实际位置。不得将 Extended AHB Master 当作通用 DDR 访问入口；该接口面向硬核本地 ILM/DLM，P3 另行冻结 fabric DDR 共享拓扑。

时钟约束沿用 [分阶段计划](ae350-migration-stages.md#频率上限测试) 与 [MUG1031-1.3.1E](https://www.gowinsemi.com/upload/database_doc/2725/document_ja/69811021aaeed.pdf) 表 4-1/4-2：

| 时钟 | P2 约束 |
| --- | --- |
| CPU core | 当前 800MHz 为配置目标，非已测稳定上限；扫 DDR 时固定 CPU profile，必要的 CPU 对照单独记录 |
| AE350 DDR_CLK | 数据内存总线，严格小于 200MHz，不能与外部颗粒 CK 混称 |
| AHB/APB | 均严格小于 200MHz，同步 1:1；优先固定当前 100MHz 条件 |
| 外部 DDR CK | 按 IP、颗粒、part/speed grade 共同支持的合法档位表征至 400MHz 目标 |
| DDR3_RW_CLK | 参考接口要求不低于 DDR CK 的 1/4，其他比例须满足实际 IP 配置 |

400MHz CK 对应 800MT/s，x32 原始峰值为 3.2GB/s；它不是可直接要求实现的有效带宽通过线。不得机械套用 20K 的 60/120MHz 或 DLL-off 参数。

## 4. 实施顺序

### A. 建立 P2 配置及启动合同

1. 阅读 P1 实现、参考 DDR wrapper/IP/约束和适用手册，记录采用的版本及哈希。保留现有 P1 恢复候选。
2. 在 Board/Machine/构建配置中显式描述 P2 的 DDR 几何、频率、参数与能力，补齐 mini 的选择和参数校验；现有 AE350 后端尚未支持完整 P2/profile，不能只往源码里塞一个未登记开关。
3. 写出 DDR ready 前后内存布局：ROM、bootstrap 栈/data、DDR、向量、测试缓冲、保留区及可发布 RAM。当前 SRAM 从地址 0 开始，而 DDR 也拟从低地址映射，必须解决所有权与切换问题，不能直接替换后让仍在使用的栈消失。
4. 可采用已确认启用的本地存储或显式译码保留区，但必须证明真实可访问；不得凭手册容量假设 ILM/DLM 已启用。若重映射，记录切换点与 cache/执行屏障。
5. 固定400MHz DDR CK交付profile，保存时钟树、SDC/CDC说明及IP参数；低频仅用于必要的故障定位。

### B. 建立可诊断的 DDR 路径

1. 接入匹配实物的 DDR IP/PHY，处理 PLL lock、reset、校准完成/失败和等待超时。
2. DDR 未 ready 时，CPU、ROM 与 JTAG 必须仍可用于诊断和恢复；禁止永久停在无法定位的等待环。非法状态要么拒绝访问并报告，要么在有界等待后失败。
3. 先验证未缓存的单次读写、byte/halfword/word 及接口支持的完整数据宽度，再验证连续访问、背压、边界和错误返回。
4. 将测试 runner/栈尽量放在已验证本地存储中，分块遍历 DDR，避免测试覆盖自身或把 cache 命中当成 DDR 正确。

### C. 验证容量、cache 和 DDR 执行

1. 在 cache 关闭/明确旁路路径上完成全容量及 alias 检查，再启用 cache 并复验。
2. 从对应 AE350/A25 文档或 SDK 确认 cache line、启停与 clean/invalidate 语义；不得沿用 VexRiscv 的自定义 CSR、指令或“fence 等于 clean”的假设。
3. 装载实际链接到 DDR 的整数程序并从 DDR 执行，证明其 PC、代码字节、栈/data 和计算结果。仅在 ROM 中执行 memcpy 不算 DDR 执行通过。
4. 验证新代码装载后的 D-cache clean、I-cache invalidate/同步和 fence.i 顺序；通过同一地址替换两份行为不同的代码证明生效。
5. 先完成单核 CPU/cache 正确性，不用 DMA 充当外部观察者。需要独立 DDR 观察时，使用已确认缓存语义的调试/内存路径，并说明它是否经过 cache；JTAG 读到一致值本身不证明 cache 已写回外存。

### D. 400MHz 交付点、压力与回归

1. 固定400MHz CK，核对 PLL、tCK、CL/CWL、刷新与PHY参数。
2. 新布局下完成校准、容量/alias、数据模式、cache、DDR执行和压力验收。
3. 配置频率与实测计时来源分列；低频历史结果不作为验收阻塞。
4. 故障保留首错，以JTAG定位；只有定位明确问题需要时才使用低频对照。

## 5. 强制验收矩阵

| 编号 | 验收项 | 通过标准与证据 |
| --- | --- | --- |
| G1 | 可复现构建 | 所用 HDL、IPC/生成参数、约束、init、Board/Machine、BSP 和工具身份进入闭包；相同输入 REUSE，修改 DDR 参数使相关组件 BUILD；20K 隔离回归通过 |
| G2 | 时序与 CDC | 每个用于实板结论的候选 setup/hold 为 0，无未解释的未约束时钟或跨域；保存真实报告，不以生成 FS 代替时序通过 |
| G3 | 校准与恢复 | 拟交付点至少 10 次复位/重新校准全部成功；记录每次 reset 类型、校准时长、状态及恢复结果；按键/软复位/重配不能统称冷启动；DDR 失败时 JTAG/ROM 可恢复 |
| G4 | 容量与 alias | 在未缓存/已证明的外存观察路径下做写入后相互扰动检查，覆盖 128/256/512MiB 边界及每个有效高地址位、末尾区域；错误几何产生的镜像不能当作可用容量 |
| G5 | 数据与掩码 | walking-1/0、全 0/全 1、交替位、地址/反码、固定种子伪随机逐块写入并读回；byte/halfword/word 掩码、相邻字节 guard、跨数据拍/cache line/row/bank 均 0 错误；记录实际覆盖字节数 |
| G6 | 全范围覆盖 | 对拟发布 RAM 完成完整分块遍历，列出 runner/栈/保留区和排除原因；容量按区间发布，未测区域不计入可用 RAM。预期 1GiB 只有在几何及相应区间验证后才能确认 |
| G7 | CPU cache | cache 关闭与开启均通过；脏行 clean 后可由独立外存路径看到新值，外存改变后 invalidate 可看到新值；覆盖整行、非整行范围及相邻 guard，cache line/对齐要求明确 |
| G8 | DDR 执行与 ISA | 实际 DDR PC、代码校验、栈/data 及结果正确；同地址代码替换后运行新版本；整数与受支持的对齐 LR/SC/AMO 最小用例通过，保留 PMP/cache 属性说明；不启用 F/D 应用作为本阶段前提 |
| G9 | 压力与性能 | 拟交付/最高声明稳定点连续至少 30 分钟内存读写/copy/跨边界混合压力，完整性错误、意外 trap、复位与校准丢失均为 0；保存起止时间、迭代与覆盖字节数。read/write/copy 及 latency 各重复 3 次，记录 cache 状态、工作集、计时来源、单位、原始结果及中位数 |
| G10 | 频率目标 | 仅验收400MHz CK交付点，记录配置与实测依据，CPU/cycle计时来源另行核实。100/200/300MHz不作为通过条件 |
| G11 | P1 恢复回归 | P2 测试后可回到已验证 P1 候选，完成 JTAG ROM 校验、控制台、trap 与恢复；记录最终驻留板卡的准确位流/固件身份 |

每类数据模式必须区分“写后立即读”与“先写完整块、再读完整块”，至少包含后者，防止总线缓冲/cache 掩盖错误。容量 alias 检查使用互不相同的标签，写高地址后重新核对低地址，不以两个地址分别读写成功代替不镜像证明。

外部可用 RAM 以已测地址区间为准，不根据 JEDEC、照片、配置位宽或单次校准成功直接发布。对实际物理容量与因保留/译码暂不可用的容量分别记录。

## 6. 构建、测试与证据合同

所有公开构建和板测继续走 `scripts/mini.py`，Windows 解释器为 `.venv/Scripts/python.exe`。扩展既有目标/参数/板测 suite，不新增第二套公开构建脚本；补齐非法平台、错误 profile、错误位流/载荷身份与测试越界的拒绝测试。

当前 P1 可用命令形式如下，P2 参数须先实现并更新 help/文档，不能把示例中的占位符作为现有 CLI 能力：

```text
python scripts/mini.py build gateware --machine mega138k_ae350 --synthesize --purpose <用途>
python scripts/mini.py board run --machine mega138k_ae350 --build <准确run> --port <板载UART> --location <FPGA位置> --cpu-jtag-location <CPU位置>
```

本机已确认的 USB 身份：FPGA 板载 USB 序列 `2025030317`、UART2 常见为 COM9；独立 RV-Debugger Plus 的 USB parent 序列 `FACTORYAIOT_PRO`。独立设备的 D2XX 通道序列为 A/B，不能直接照搬 USB parent 序列；location 和 COM 每轮复核。接线、电平和现有后端见 [CPU JTAG](ae350-jtag.md)。`docs/lab-environment.md` 主体描述旧 20K，不得照搬其 COM4、IP、DDR 或下载器选择。

每个频点/候选保存以下机器可读字段，数值未知用 null，未执行项目用 not-run，不得默认 0/pass：

```text
source_commit / dirty / source_input_hash
machine / board_identity / device_part / artifact_digest
bitstream_sha256 / firmware_sha256 / DDR_IP_hash / profile
geometry / timings / configured_clocks / measured_clocks
calibration_runs / calibration_time / calibration_status
tested_ranges / reserved_ranges / tested_bytes / patterns / seeds
cache_mode / cache_line_bytes / maintenance_sequence
stress_start / stress_end / elapsed_seconds / iterations
errors / first_error / trap_registers / recovery_result
bandwidth_samples / latency_samples / timing_source
```

`first_error` 至少记录地址、预期值、实际值、模式/种子、cache 状态及频点。压力累计覆盖量与地址空间覆盖量分开计数。报告单列硬件温度/散热、供电条件、仪器及未覆盖环境；不能从室温单板测试推断所有温度/电源条件稳定。

## 7. 交付物与完成判定

1. 实现代码、机器/DDR profile、BSP/cache 支持和有针对性的主机/RTL回归。
2. `docs/ae350-p2-bringup.md`：地址图、bootstrap 切换、时钟/复位/校准、IP 再生成步骤、运行与恢复命令、cache 合同和限制。
3. `reports/ae350-p2.md` 及配套 JSON：按 G1～G11 逐项列 pass/fail/not-run、准确 artifact/run 引用、每个频点结果及首错。
4. 受管理 build/runs/store 中的完整原始日志、PnR/参数、测试载荷和恢复证据。所有失败保留，不能覆盖原始 run。
5. 更新主阶段计划和机器能力描述。完成后整理阶段提交与可复现快照；不主动 push/tag/发布，不修改现役引用。

G1～G11 全部满足才能标 `P2 complete`。若 400MHz 无法通过，保留其失败结论并标 `P2 partial / target unmet`，报告已验证频率、容量和功能边界；不得自行把较低点改写为原目标。若硬件型号/时序/IP 工具缺口阻止安全配置，提交已完成的调查、代码和证据，准确说明阻塞项，不编造通过结果。

## 8. 可直接转交给执行 agent 的提示词

> 请从 riscv-mini 提交 872bc1c981b1486dc696cc06feb5af77687213f9 开始，执行 docs/ae350-p2-task.md 中的 AE350 P2 DDR/cache 任务。先核对 P1 基线和恢复方法，阅读只读的 TangPrimer-138K 参考资料，给出 DDR 几何、启动内存布局、时钟/复位和测试路径，再按 A～D 推进实现与上板；采用本文最新零基址DDR布局，仅400MHz CK是强制DDR频点。不要增加 UART 调试硬件；故障定位使用现有 CPU JTAG。保持 mini 为唯一公开入口，保留 20K/P1 的引用与恢复能力，不接外设 DMA/HDMI，不写 Flash或进行未经授权的断电冷启动。不得止步于计划、RTL 生成或 PnR；在可用硬件上完成 G1～G11 的验证，保存准确产物、400MHz配置与首错证据。现有 P1 余项与 P2 状态分别报告；低频可读写不等于 P2 完成。有真实外部阻塞时明确记录，不虚报验收。完成后交付代码、操作文档、逐项验收报告和可复现阶段快照。
