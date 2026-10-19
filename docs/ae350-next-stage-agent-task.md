# AE350 P2 内存整改：实现交接

交接时间：2026-10-19。接手目标是完成当前内存系统整改并按 M1–M9 交付；当前只是第一批结构改动通过，不是 P2 完成。先读本文件，再按任务分支读取引用资料。

## 1. 接手点与约束

仓库根目录为 `C:/Users/bearice/Workspace/TangPrimer-20K/riscv-mini`，不是外层 TangPrimer-20K。HEAD 是 snapshot `cf020fe9b890163da0728ccd447fb66238e6b79d`。此后改动全部在未提交工作树，包括新增文件和启动固件目录迁移；先检查 `git status`，继续现有改动。仅 checkout 此 commit 会丢失当前实现。转交其他 checkout 时需同时转交完整工作树；实板回执和构建产物在被忽略的 `build/` 中，Git 本身不包含这些证据。

唯一当前验收合同：[ae350-p2-memory-acceptance.md](ae350-p2-memory-acceptance.md)。具体实现与回执：[整改报告](../reports/ae350-p2-memory-redesign.md)。旧 `reports/ae350-p2.md/json` 和 review 报告是历史候选证据，不能代替新 M1–M9。

用户决定：

- 启动固件是后续阶段共用组件，源目录 `firmware/bootloader/ae350/`，产物身份 `AE350-ROM-bootloader`。当前 `--ae350-profile p2` 只选择硬件集成配置。应用运行时在 `firmware/bsp/mega138k_ae350_payload/`，阶段诊断在 `firmware/payload/ae350_p2_tests/`。
- 厂商可初始化 ITCM 承载启动镜像；硬核 ILM/DLM 各 64 KiB 承担本地代码、数据与栈。fabric 工作 SRAM、自定义 P2 ROM AHB 桥、JTAG probe/magic mailbox/常驻 launcher 已移除。
- 普通应用和诊断通过同一 UART loader 装载。JTAG 只调试、观察和显式恢复。继续复用正式 `mini.py build/board` 入口与 `HardwareSession`，避免为单次验收添加私有 host-op 或第二条装载协议。
- DDR CK 400 MHz 是唯一必需交付点；训练由 Gowin IP 自动执行。数据口频率优化仍需做，不能把取消 DDR CK 扫频误读为数据 AHB 固定 100 MHz。
- 只编程 FPGA SRAM，不写 Flash，不改 20K current/baseline。30 分钟长跑、100/200/300 MHz DDR CK 扫档和物理冷启动不属于必需项。
- XIP 仅预留地址，DMAC、多主仲裁、HDMI 后置。现配置 fabric 存储原子操作不受支持，继续保持能力合同明确。

## 2. 当前可信候选与证据

| 内容 | 当前身份 / 回执 |
| --- | --- |
| 写聚合前 gateware | `6a2e1381d170b6b33f61aa094106c6f29c5714e93063b7c62bdfd46a6cbc1fac` |
| 写聚合前构建 | `build/runs/20261019T092115491474Z-build-candidate-97cda7/result.json` |
| 丢失请求的写聚合 gateware | `03c3768cd4aa8c16caccbeba25f6525f32b5d8003979855ffee73fc0437532c1` |
| 该次构建 | `build/runs/20261019T103704769619Z-build-candidate-997c16/result.json` |
| 当前 gateware | `272f52128db04966b5ead1e5c5f0f30fbd802a5628a9aba300e079257b5bbba3` |
| 当前构建入口回执 | `build/runs/20261019T112810962889Z-build-candidate-3a0c0d/result.json` |
| 当前 bootloader | artifact `b22414da9d27`，固件身份 `f00df5ff325b`。适配器能力描述变更后重建；此前身份是 `7ced7b131570` |
| 诊断 payload | artifact `55610f741b2d`；`build/runs/20261019T044919229629Z-build-candidate-20a1b0/result.json` |
| 普通应用 payload | artifact `12694066993c`；`build/runs/20261019T044921080716Z-build-candidate-c5d35d/result.json` |
| 写聚合前 DDR suite | `build/runs/20261019T092305840858Z-board-ae350-ddr-suite/result.json`：UART 装载，12 checks / 0 failures；同一次 write-back 为 4 条 UI 写 |
| 丢失请求的 DDR suite | `build/runs/20261019T105117821696Z-board-ae350-ddr-suite/result.json`：正常 write-back 为 1 条 UI 写，但后台排空会丢掉已接收的下一请求 |
| 当前 DDR suite | `build/runs/20261019T113159146665Z-board-ae350-ddr-suite/result.json`：SRAM 编程 location `107569`，UART `COM9`，12 checks / 0 failures，mcause=0，`flash_written=false`。写计数 `48453, 48453, 48454`，读计数 `45187, 45188, 45188, 45189`。`read_control=0x0f6b`、`write_control=0x1f6b` |
| 新候选只读核验 | `build/runs/20261019T092409744126Z-board-ae350-jtag-test/result.json`：ITCM 身份/非顺序读、ILM/DLM、自主 POST 通过 |
| 重复构建 | `20261019T092358729718Z-build-candidate-06b146`：bootloader/gateware 都 REUSE |
| UART 拒绝路径 | 更名前同结构候选 `86ea3a8393df` 的 `20261019T045843693507Z-board-ae350-upload`：ABI/header/range/truncated/CRC 拒绝后正常上传通过；不要称为新候选重复实测 |

当前 STA setup=0、hold=0、最小脉宽 slack=+0.183 ns，对应 gateware `272f52128db0`。`20261019T113159146665Z-board-ae350-ddr-suite` 把该 bitstream 写入 FPGA SRAM，CPU 结束后处于 running；SRAM 掉电即失，接手时仍需确认实际状态。

该 suite 只遍历 1 MiB 区间，实际覆盖 1,039,328 字节、32,479 个 32 B 块，排除完整运行镜像。容量标签通过不等于全 1 GiB 遍历。短压力只有配置的 2 秒核心周期预算、2 轮；整套载荷墙钟 23.9 秒，其中运行约 23.1 秒，不是压力时长。

硬核事务属性仍是 SEQ、WRAP4、HSIZE=3（64-bit）、HPROT=0xf。ILM 测量代码 + DLM 栈的 cache 观察：refill=1 条 UI 读、hit=0 条、invalidate 后 refill=1 条；同一次 32 B write-back 为 1 条 UI 写。写聚合前候选上同一次 write-back 为 4 条。

## 3. 还差什么

下表是接手状态，不替代合同中的完整完成条件。

| 合同项 | 已有结果 | 接手者需闭合 |
| --- | --- | --- |
| M1 本地内存 | ILM/DLM 配置、设栈前全范围两种地址相关图案、ILM 代码执行、DLM 栈；ITCM 初始化通过 | 故障注入与恢复边界、应用 ILM 覆盖后 ROM 重入的可靠性；ECC 保持关闭需明确，若启用则补相应证据 |
| M2 burst 读 | 同块 4×64 / 8×32 仿真只发 1 条 UI 命令；写聚合 gateware 上真实 refill 仍为 1 条 UI 读 | 后续时钟修改后重新核验，保持 burst 边界与独立请求不复用的语义 |
| M3 写通路 | `03c3768cd4aa` 会在后台排空时丢掉寄存译码已经接收的下一请求（审查 `reports/ae350-write-aggregation-review.md`）。`272f52128db0` 把该请求的地址、控制和写数据保留到事务完成。正式回归覆盖 BUSY→读、BUSY→独立写、跨块、排空成功、超时和校准失效。STA setup/hold=0/0，最小脉宽 +0.183 ns。`20261019T113159146665Z-board-ae350-ddr-suite` 复测：store 不进总线，随后一次 32 B write-back 仍为 1 条 UI 写 | 后续时钟或写缓冲改动后，用同一观察再核验这 1 条 UI 写，并保留 BUSY 后与后台排空重叠的回归 |
| M4 时钟 | data AHB 100 MHz、外设/ITCM 80 MHz、UI 100 MHz、CK 400 MHz 独立配置与约束 | 明确硬宏合法条件、试验合理更高数据口频率、实板与 STA 选择交付点；核心实际频率未独立标定 |
| M5 协议/CDC | 真实异步 FIFO、注册译码、读 burst/错误恢复定向回归与 STA 通过 | 独立域复位、失锁/失校准、聚合缓冲排空、超时/迟到响应等最终结构回归；现有通过不能自动覆盖新写缓冲 |
| M6 自举 | 自主 POST、单 UART loader、正常/错误上传通过；JTAG 不装载 | DDR 不可用时可恢复、ILM 被应用占用后的 loader 重入/重复上传验证；关键失败状态需如实报告 |
| M7 完整性 | 当前候选 1 MiB 遍历、cache、DDR 执行 | **最终候选**全发布区间遍历、lane/掩码/别名、至少 10 次复位重新校准与排除区说明 |
| M8 性能 | 三次 raw cycles 与 UI 计数保留 | 相同工作量/cache 条件的基线比较、有效字节与命令数；独立计时短压力、核心时钟依据；当前部分 uncached read 反而更慢 |
| M9 工程交付 | 独立产物、统一入口、ABI、依赖闭包和回执 | 最终独立 P1 恢复回归后恢复 P2，更新逐项状态、归档实际依赖与证据；JSON 汇总仍按旧 G 项生成，不能用它自动判定 M1–M9 完成 |

资源边界：整机 BSRAM 当前 64 个，旧候选 54 个；厂商 ITCM 最小 32 KiB 配置增加资源，不能因删除工作 SRAM 就宣称净节省。厂商 fabric ITCM 与硬核 ILM 是不同资源。

## 4. 建议推进顺序与完成条件

### A. 先冻结写聚合的协议语义，再实现

阅读 `gateware/machines/ddr3_ahb.py`、`ae350_interconnect.py`、`tests/ae350_burst_test.py`。现状为单笔在途、390-bit 写请求 / 65-bit 响应、深度 2 的真实异步 FIFO；同一 32 B 行的顺序写合并为一条掩码 UI 写，读 burst 仍只复用同一合法块。注册译码打断硬宏到适配器长路径，保留该时序边界。`272f52128db0` 已把寄存译码收下的下一请求连同地址、控制和写数据留到排空之后再执行。主机回归、STA 和 `20261019T113159146665Z-board-ae350-ddr-suite` 的 cache 写回实测已经对齐。`03c3768cd4aa` 上的正常写回不能代替这条路径。

先记录：AHB 写何时算完成、已应答数据归谁持有、后续读如何转发/先排空、burst 提前结束/BUSY/跨块/独立写如何处理、复位/超时/校准失效时错误如何报告。AHB beat 需先完成主机才会给后续 beat，单纯等齐四拍再应答会死锁；若提前应答，需明确异步写失败无法追溯为已完成事务 HRESP 的边界与可观察故障机制。

完成条件：设计依据与 RTL 一致，完整 WRAP4 write-back 同块 32 B 合并为 1 条 UI 写命令；部分写/尾块不改邻接字节。测试覆盖 byte-valid/mask、写后读、读写交替、提前结束、背压、错误、超时、迟到响应及复位。以真实 FIFO 联合仿真和 CPU cache 写回实测共同取证。

### B. 做时钟与协议收敛

配置与合法范围从 `ae350_map.py`、`modular/ddr_inputs.py`、硬宏手册及 PLL/SDC 实现追溯。当前 100 MHz 是已验证运行点，尚非理论上限。分开记录 core/data AHB/peripheral AHB/APB/UI/CK，保持 DDR CK 400 MHz。改变数据时钟时复核 CDC、reset 与 timing groups，完整 STA 包含最小脉宽。

完成条件：交付频点有合法性来源、完整 STA 和实板证据；失败点记录真实最坏路径。不能把少量失败直接归因加密宏或通过同域 false-path 宣布成功。若频点收益不成立，可保留 100 MHz，但需要测量与选择理由。

### C. 冻结最终结构后补验收

复用现有诊断，完成 M7、M8、M9；在应用中测量主机请求之间的真正运行窗口与暂停，必要时先改善现有 runner 的结果结构。最终全范围和重复校准放在最后结构/时钟确定后做，避免反复为过渡镜像验收。

完成条件：M1–M9 逐项列出 PASS/未完成及回执；性能用同条件三次样本和原始数值支持，不用 UI 命令减少推导所有负载加速。最后通过 P1 回归并恢复最终 P2，记录驻留身份。提交/推送/正式发布按用户后续指令进行。

## 5. 代码导航与已知陷阱

- `gateware/machines/ae350_map.py`：地址/保留区/ABI 唯一来源，生成 C 头和链接片段。DDR 零基址；ILM `0xa0000000`，DLM `0xa0200000`，ITCM `0x80000000`，DDR 诊断 APB 页 `0xf8003000`。不要重新引入 `0x40000000` 工作 SRAM或 ROM 尾部诊断 mux。
- `mega138k_ae350_p2.py`：当前硬件集成、APB setup 拍寄存读、快照 CDC。组合 APB 回读 mux 和过宽探针曾破坏时序；诊断需有界快照，避免完整 256-bit 组合导出。
- `firmware/bootloader/ae350/`：常驻启动固件；`start.S` 在设栈前检查/初始化硬 LM，ILM 热代码从 ITCM 装载；应用可覆盖 ILM，重入需显式恢复所有权。ECC 检查当前关闭。
- `firmware/bsp/mega138k_ae350_payload/`：DDR 入口、ILM LMA 复制和 DLM 栈；遍历排除区包括 ILM 的 DDR load image、data/bss。
- `scripts/modular/ae350.py`：独立 bootloader/payload、厂商 ITCM 四个 byte-lane 初始化文件及源码输入闭包。profile 未来共用启动固件时复用组件，不复制 stage 专属目录。
- `mini_ops/ae350_ddr_suite.py` / `ae350_upload.py` / `ae350_payload.py`：UART 统一装载；参数由 ELF 符号及 artifact sidecar 定位，严格检查后生成普通上传 CRC。JTAG 仅 halt 后读状态，resume 后再次读取前须重新 halt。
- CPU running 时 abstract memory 访问会报 `08000404`；清 haltreq 不会恢复 hart，显式 resume 才会。`close()` 会主动恢复 CPU。不要再归因为 FTDI 句柄或推导 CPU 地址窗口上限。
- cache 观察代码必须在 ILM、栈在 DLM，测量段不能打印 UART；从 DDR 执行观察器会把自身取指计入 DDR 事务。
- `tests/ae350_burst_test.py`、`tests/ae350_remediation_test.py`、`scripts/tests/test_ddr3_ahb.py`、`test_ae350_interconnect.py` 为主要回归。接口桩适合隔离逻辑，CDC 验收必须有真实异步 FIFO。

## 6. 接手复现命令

PowerShell，在仓库根目录执行，Python 使用 `.venv/Scripts/python.exe`。先按仓库 AGENTS 与 build/test skills 检查环境。以下 location 是本轮观测值，USB 重枚举会变化：FPGA `107569`（IDCODE `0x0001081b`），独立 CPU `6673`（IDCODE `0x1000563d`），UART `COM9`。板卡身份验证通过后才编程。

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -p 'ae350*test.py'
.venv/Scripts/python.exe scripts/tests/test_ddr3_ahb.py
.venv/Scripts/python.exe scripts/tests/test_ae350_interconnect.py
.venv/Scripts/python.exe scripts/mini.py build gateware --machine mega138k_ae350 --ae350-profile p2 --synthesize
.venv/Scripts/python.exe scripts/mini.py build payload --machine mega138k_ae350 --ae350-profile p2
```

本轮 scoped host 结果：AE350 50 tests、mini operations 38、modular build 37、线缆 7；adapter 61 checks、interconnect 15 checks。原始日志在 `build/reports/ae350-memory-redesign-20261019/`。数字只是本轮快照，接手以实际测试输出为准。

以下复现当前候选。修改源码后，应将 `$gw` / `$payload` 换为新构建的实际回执；不要拿新源码配旧镜像声称已验收。

```powershell
$gw = 'build/runs/20261019T092115491474Z-build-candidate-97cda7/result.json'
$payload = 'build/runs/20261019T044919229629Z-build-candidate-20a1b0/result.json'
.venv/Scripts/python.exe scripts/mini.py board jtag-test --build $gw --location 107569 --cpu-jtag-location 6673
.venv/Scripts/python.exe scripts/mini.py board ddr-suite --build $gw --payload $payload --port COM9 --location 107569 --cpu-jtag-location 6673 --flags 15 --traversal-mb 1 --stress-seconds 2 --timeout 120 --poll-interval 1
.venv/Scripts/python.exe scripts/mini.py board recover --machine mega138k_ae350 --build $gw --port COM9 --location 107569 --cpu-jtag-location 6673
```

这些命令会重编程 SRAM/运行载荷，串行执行。全区间、重复校准和最终 P1 回归的参数先查当前 CLI/help 与实现，确认它们真的覆盖目标条件；若入口能力不足，改进现有操作并给出有意义的回归，不用临时独立脚本替代正式路径。

## 7. 交接时更新的文档

- 本文件：替换简短旧任务说明，作为接手入口，提供未完成项、执行顺序、完成条件、候选与复现方法。
- [P2 task](ae350-p2-task.md)：指向本交接入口；[验收合同](ae350-p2-memory-acceptance.md) 仍是唯一规范。
- [bring-up](ae350-p2-bringup.md)：当前启动/地址/操作方法；[bootloader](ae350-bootloader.md)：公共启动组件归属。
- [整改报告](../reports/ae350-p2-memory-redesign.md)：当前工作树改动和历史实测证据；本次只编写交接，不新增硬件验证结论。
