# 多平台目录重组 — 阶段 E 收口报告

日期：2026-10-21。范围：`primer20k_vexriscv` 与 `mega138k_ae350` 平权机器包、设备解耦、scripts/mini_ops/tests 归属、firmware 树重组。计划见 [repo-reorg-execution-plan.md](repo-reorg-execution-plan.md)，阶段 0 基线见 [reorg-stage0-baselines.md](reorg-stage0-baselines.md)。

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
- 旧发布包在新树上 `build --locked` 失败是预期（见 [component-builds.md](component-builds.md)），历史复现只用 `mini.py builds reproduce`。
