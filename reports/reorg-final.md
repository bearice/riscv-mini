# 多平台目录重组 — 阶段 E 收口报告

日期：2026-10-21。范围：`primer20k_vexriscv` 与 `mega138k_ae350` 平权机器包、设备解耦、scripts/mini_ops/tests 归属、firmware 树重组。计划见 repo-reorg-execution-plan.md（文档已移除，见 Git 历史），阶段 0 基线见 [reorg-stage0-baselines.md](reorg-stage0-baselines.md)。

## 1. 提交链

| 提交 | 内容 |
| --- | --- |
| `284e10d` | 修 `test_build_report` 的陈旧 `pnr_report` import（阶段 0 前置） |
| `aec1fd1` | 阶段 0：两机 plan、host 测试清单、PnR 基线、`build/reorg-manifest.json` |
| `f2dda9e` | 裁决修订四：20K full-profile PnR 退出阶段门 |
| `f5fecbc` | 阶段 P：共享 boot/framing/compile 符号抽层 |
| `405a16c` | 阶段 A：两机平权包 + 闭包声明 + 不落回 20K 的机器注册表 |
| `4c8d23a` | 阶段 B2：I2S 麦克风设备核心 |
| `8fa5be6` | 阶段 B3：PT8211 音频设备核心（含 360 周期等价性对照） |
| `6038d46` | 阶段 B4：LCD 扫描/palette/背光设备核心（RTL 逐字节一致） |
| `24feaff` | 阶段 C：firmware 按机器分目录 |
| `29f898b` | 阶段 D：scripts/mini_ops/tests 按机器分包 + 注册表分发 |
| 本提交 | 阶段 E：full-profile 路径修复、收口文档与终态验收 |

## 2. 终态验证门

- **AE350 gateware**：`build gateware --machine mega138k_ae350 --synthesize`（run `20261021T131058958406Z-build-reorg-stageE-ae350-b1380d`）→ gateware `84514c545437`，`timing_report = {setup 0, hold 0, min_pulse_width_slack_ns 0.142}`，与阶段 0 采纳的 `44405c059cf8` 基线一致。
- **20K minimal 锚点**：`build system --profile minimal --synthesize`（run `20261021T131443480138Z-build-reorg-stageE-20k-minimal-a964c0`）→ setup 0 / hold 0，最差 setup slack +4.214 ns（`VexRiscv/decode_to_execute_SRC2_CTRL_1_s0` → `memory_arbitration_isValid_s1`，CLKDIV/CLKOUT 16.667 ns），Logic 6284/20736、Register 3596/16173、CLS 4574/10368、BSRAM 13/46 — 与阶段 0 锚点逐项相同。
- **20K full-profile（裁决修订四：原样登记）**：`build system --profile full --cpu-verilog <v0.11.0 归档 CPU RTL> --feature fpu=off --synthesize`（run `20261021T130549318960Z-build-reorg-stageE-20k-full-r2-ccd7d2`）→ 综合与布线完成，**setup 50 / hold 0**，Logic 19113/20736（92%）、Register 10830/16173、CLS 10161/10368（98%）、BSRAM 37/46。仍红，属搬迁前既有的 RTL 规模增长回归，另行立项修复，不计入本次重组。
- **host 测试**：`build/reorg-stageE-hosttests.json`，58 个目标 → 39 pass / 13 fail / 6 skip。13 个失败全部是需要 `--iverilog`/`--cpu`/`--output-dir` 等驱动参数、且本机未装 Icarus Verilog 的 verilog 驱动式测试（历史已知红）；`modular_reuse_test` 本次转绿（源回放、仅 BIOS 重建、硬件复用、no-op 复用四项通过）。
- **隔离反证**：改 `mega138k_ae350/peripherals/board_io.py` 只变 138K 输入键；改 `primer20k_vexriscv/peripherals/sd.py` 只变 20K 键；改 `devices/gpio/core.py` 两键都变。
- **CLI/plan**：`mini.py` 全部子命令 `--help` 可加载；`plan system`（20K）与 `plan gateware --machine mega138k_ae350` 正常；AE350 `plan system` 仍原样输出 `system BLOCKED  P1 backend not implemented; no generated RTL or runnable system`；未知机器 ID 直接报错。

## 3. 本阶段发现并修复的缺陷

- **full-profile 独有路径缺陷**：`gateware/machines/primer20k_vexriscv/peripherals/usb_ultra.py` 的 vendor 路径多拼了一层 `gateware/`，minimal 不实例化该 PHY 所以阶段 A–D 未暴露；full 综合报 `Cannot open Verilog file 'gateware/gateware/vendor/usb_fs_phy/usb_fs_phy.v'`。改为 `parents[3]/'vendor'` 后 full 综合通过。
- **parser 期机器静态 import**：`scripts/mini.py` 在构造 `ddr-suite` 参数时 import AE350 `address_map`，使 20K 的保留源码 checkout 回放失败。该 import 无实际用途，删除；`modular_reuse_test` 由此转绿。
- **组件 own 列表**：`firmware.py` 的 own 列表原先整目录 `scripts/modular`，会把另一台机器的后端也哈希进 20K 组件键；改为显式列出框架模块 + 本机包，源回放所需的最小集合以显式文件清单声明。

## 4. 与计划 §1 的差异（已登记）

- `tests/machine_closure_test.py` 放在 `tests/common/`（跨机闭包检查，不属于任何单机）。
- `sim/` 并入 `tests/primer20k_vexriscv/sim/`，`sim/README.md` 跟随；第三个测试目录取消。
- `scripts/modular/arguments.py` 留在框架（纯 CLI 面），`release()` 参数声明也移到框架；机器包只读命名空间。
- `scripts/primer20k_vexriscv/linux_console_perf.py`（性能驱动，非测试）从 tests 清单中按驱动处理。
- manifest 里重复列出的 `tests/linux_console_perf.py` 只落一次，落点为上述驱动路径。

## 5. 边界

- 未做 HDMI、`hal/src` 跨机重构、`interfaces/requires`；这些仍在计划 §5“不做”。
- 全部验收为主机侧：无实板烧写、无 Flash 写入、无真实外设观测。full-profile 的 50 条 setup 违例只登记，未修。
- 旧发布包在新树上 `build --locked` 失败是预期（见 [component-builds.md](../docs/common/component-builds.md)），历史复现只用 `mini.py builds reproduce`。

## 6. 验收评审（gemini-4）与修复

独立评审（antigravity/gemini-4-argon，只读，对照提交链 `aec1fd1..4a0fe4c`）结论为 **BLOCKED**，理由分五类，逐条复核后全部成立并已修复：

- **B1 框架未定义名字**：`scripts/modular/targets.py` 的 `output_view()` 引用了未定义的 `machine` 与 `configuration`（stage D 拆分后遗留），任何带 `--output-dir` 的 system 构建都会 `NameError`。改为 `machines.system_machine(store, ref)` 与 `machines.part(machine, 'configuration')`。
- **B2 搬迁后 import/sys.path 残留**：`tests/primer20k_vexriscv/sim/test_video.py` 仍从旧适配器取 `WIDTH/HEIGHT`（B4 已移到 `devices/video/core.py`）；`tests/common/test_board_events.py` 仍插入已删除的 `scripts/tests` 并 import 兄弟模块；`test_audio_capture.py`、`test_boot_image.py`、`doom_debug.py`、`doom_verify.py`、`gwbasic_verify.py`、`mini_operations_test.py`、`firmware_verify_test.py` 只把 `ROOT/scripts` 放进 `sys.path` 却 import `scripts.*`。上述 8 处全部改正；`firmware_verify_test.py` 同时修掉 `import scripts.x.y` 后仍用裸名的 `NameError`，`mini_operations_test.py` 的 mock 目标改为 `scripts.primer20k_vexriscv.boot_upload.*`。
- **B3 AE350 输入仍是 glob**：`ae350.py` 的 `_inputs()` 用两处 `.glob('*.py')` 收集脚本且无 `source_closure()`。改为显式 `AE350_SCRIPTS` 允许列表 + `source_closure(root)`（缺文件即 `ValueError`），并补上此前漏登记的 `scripts/modular/machines/__init__.py`。
- **B4 框架/shared tools 仍直连机器包**：`cli.py` 在构造 parser 时就 import 20K release（并硬编码两处）；`worker.py` 直连 20K `system/firmware/rtl`；`targets.py` 用 `machine == AE350` 分支与三次硬编码机器名；`prepare_commit.py`、`build_records.py` 直连 AE350/20K 后端。现统一走 `scripts.modular.machines` 注册表（新增 `release()`；`description()` 再导出 `timing_report`），未知机器不再落入 20K 管线。
- **B5 文档破坏**：`docs/platforms/primer20k_vexriscv/ddr-boot.md` 相对链接指向已删除的 `../gateware/ddr_boot.py`；`scripts/README.md` 把 `board verify --suite firmware/boot/cold` 误改成 `firmware/bsp/primer20k_vexriscv/cold`（与 `board.py` 的判据不符）；`gateware/vendor/README.md` 出现双重前缀路径；`tests/primer20k_vexriscv/sim/README.md` 的示例命令仍是旧 `sim/test_*.py`。全部改正。

修复后的复验（本提交）：

- **20K minimal 全流程**：run `20261021T140849117005Z-build-reorg-review-20k-minimal-r2-8db40c`，setup 0 / hold 0，最差 setup slack +4.214 ns（同一路径、同一 CLKDIV/CLKOUT 16.667 ns 关系），Logic 6284/20736、Register 3596/16173、CLS 4574/10368、BSRAM 13/46；`--output-dir build/tmp/view-20k` 同时验证了 B1 的 materialize 路径（build id `4a0fe4c-minimal-rv32im-rom8k-l24k-build-248ffa7a-82f01209`）。
- **AE350 gateware**：run `20261021T141037438388Z-build-reorg-review-ae350-cc8879`，gateware `28acfbbc4552`，`timing = {setup 0, hold 0, min_pulse_width_slack_ns 0.142}`。
- **20K full-profile RTL 生成**：gateware `5e47b4ab92fe`；与 B4 的 RTL 快照逐文件比对**完全一致**（`build/rtl-b4.json` vs `build/rtl-review.json`），说明 audio FIFO 改为 property 属结构性整理，不改变网表。
- **host 全量扫描**：`build/reorg-final-hosttests.json`，83 个目标（`*_test.py` 与 `test_*.py` 两种命名、含 `tests/<machine>/sim/`）→ 61 pass / 3 fail / 19 skip-or-needs-args。3 个红全部为搬迁前既有：`sim/test_audio.py`（环形缓冲基址常量已出该机 RAM 窗口）、`sim/test_sd_dma_bounded.py`（在 5acca4d 树上逐字复现同样断言失败，从未进入旧扫描清单）、`test_features.py`（`fpu` 已关后仍断言全特性为真）。
- **隔离反证**：`build/stage_d_isolation.py` 三项仍全部通过；`mini.py` 全部子命令、`doctor`、两机 `plan`、未知机器报错均正常。

已知红清单（本阶段不修，统一测试修整时处理）：上述 3 个主机测试，以及 13 个需要 `--iverilog`/`--cpu`/`--output-dir` 且本机无 Icarus Verilog 的驱动式 verilog 测试。

环境副作用（非缺陷，记录备查）：在全新 workspace 第一次起 Gowin PnR 时 `gw_sh` 偶发静默退出（rc=1、无日志），同 workspace 重跑即成功；stage 0 亦观察到同样现象。

### 6.1 第二轮评审补正（同一评审人，针对 `a24e1e8`）

第二轮确认 B1/B3/B4/B5 已闭环，但指出 B2 只修了被点名的 8 个文件，同类 Stage-D 改写缺陷仍在三处；已全部修复（本次提交）：

- **4 个 AE350 生产模块会在运行期 `NameError`**：`ae350_upload.py`、`ae350_ddr_suite.py`、`ae350_dma_suite.py`、`ae350_io_suite.py` 里 `import scripts.mini_ops.mega138k_ae350.ae350_image` 只绑定 `scripts`，后面的 `ae350_image.build/abi_tag/LOAD` 全部未定义——即任何 `mini.py board ... --machine mega138k_ae350`（含 `--dry-run`）都会崩。统一改为 `from mini_ops.mega138k_ae350 import ae350_image`，与同文件其余 import 一致。
- **`tests/mega138k_ae350/ae350_memory_test.py`** 的 `from modular import ae350`、`from mini_ops import ae350_upload` 已随 Stage D 失效，且两处 `import scripts.mini_ops...` 同样留下裸名引用；改为从新包导入后 33 个用例全绿（该文件此前被扫描器列入 `SKIP`，因此两轮扫描都没暴露）。
- **`tests/primer20k_vexriscv/linux_port_test.py`** 的 `sys.path` 缺 `ROOT`，且仍从旧 `tests/` 导入 `mini_operations_test`；补齐后 47 个用例全绿。
- **`scripts/mini_ops/primer20k_vexriscv/artifacts.py`** 用裸名 `tools.boot_image`，在没有 `ROOT/scripts` 的导入环境下 `ModuleNotFoundError`；改为 `scripts.tools.boot_image`。

同时补掉评审列出的 5 条 minor：`test_audio_capture.py`/`test_boot_image.py` 重复插入的 `sys.path` 行、`Microphone.fifo` 与 `Audio.fifo` 一致的 property 化、`ae350.py` `rtl()` 里第二处重复 import、`PRIMER20K_CLOSURE` 补上 `scripts/modular/machines/__init__.py`、扫描器 `SKIP` 收窄到唯一真正开串口的 `bios_ls_serial_test.py`（其余 5 个纯 host 用例已纳入常态扫描，并全部转绿）。

复验（本提交）：

- **host 全量扫描**：`build/reorg-final2-hosttests.json`，83 个目标 → 66 pass / 3 fail / 14 needs-args。3 个红仍是既有（`sim/test_audio.py`、`sim/test_sd_dma_bounded.py`、`test_features.py`）；`ae350_memory_test.py`（33 例）与 `linux_port_test.py`（47 例）本轮转绿。
- **20K minimal**：run `20261021T151114537880Z-build-reorg-review2-20k-minimal-564f23`，setup 0 / hold 0，最差 +4.214 ns（同路径同域），Logic 6284/20736、Register 3596/16173、CLS 4574/10368、BSRAM 13/46。
- **AE350 gateware**：run `20261021T151250260136Z-build-reorg-review2-ae350-faee1a` → `REUSE 28acfbbc4552`，timing 仍 `{0, 0, 0.142}`；输入键未变，证明本轮的麦克风/音频改动不触及 138K。
- **20K full-profile RTL**：gateware `6cf7a0f905e5`，与上一轮 `build/rtl-review.json` 逐文件一致（`build/rtl-review2.json` 比对：无新增、无删除、无变化），即 FIFO property 化不改变网表。

### 6.2 第三轮评审（结论 ACCEPT）与收尾

第三轮（针对 `c8d9a61`）**ACCEPT**，无阻断；四条 minor 中三条属代码、一条属历史回执，处理如下：

- `scripts/primer20k_vexriscv/boot_upload.py` 直接执行时缺 `sys.path` 引导（`python scripts/primer20k_vexriscv/boot_upload.py --help` 会 `ModuleNotFoundError: tools`）；已补 `parents[2]` + `parents[2]/'scripts'`，`--help` 正常。
- `artifacts.py` 只把 `ROOT` 放进 `sys.path`，其 `boot_image` 依赖 `tools.*`；`probe.py` 与 `ae350_upload.py` 的引导行在原 import 之后。前者的路径改为 `[ROOT, ROOT/'scripts']`，后两者把引导行提到被使能的 import 之前（`ae350_upload.py` 的次序整理在 `21103ef` 里漏改，由其后一个收尾提交补齐；该文件不在任何构建闭包内，不影响输入键）。
- `ae350.py` 的 `rtl()` 里第二处重复 `address_map` import（与 `DDR_PROFILE` import 交错，上一轮的对偶替换没命中）已删除；因此 AE350 输入键变化，重新 PnR 得 gateware `24e74f6ce918`，timing 仍 `{0, 0, 0.142}`（run `20261021T153506996468Z-build-reorg-review3-ae350-bd4949`）。
- `reports/ae350-p2.md` 里的 `../scripts/ae350_report.py` 链接保持原样：它是历史验收回执，按计划"旧回执不再复核"且在登记保持不改的清单内，不做追溯改写。
