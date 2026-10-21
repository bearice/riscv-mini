# 重组阶段 0 —— 基线与 manifest 记录

生成：2026-10-21。基线树：`5acca4d` + `284e10d`（`sim/test_build_report.py` 的 `scripts.build` 死 import 修复，独立小提交，复跑通过）。本文是 [多平台目录重组执行计划](repo-reorg-execution-plan.md)（修订三）阶段 0 的记录件；阶段 A–D 的对比门一律以本文为基线。构建均走 `scripts/mini.py` 托管流程；本阶段未写板卡 Flash、未跑板卡操作。

## 1. 两机 plan 记录（当日实测）

20K（`build system --profile minimal`，run `20261021T090722914537Z-build-reorg-stage0-baseline-20k-minimal-r1-a0db6c`）：

```text
soc-description REUSE 5ecf37a07cdc
bootloader      REUSE c7d677b72324
gateware        BUILD 030dc2c45ab4
hal-sdk         REUSE bde965fc7228
bios            REUSE b4c8ce696339
```

AE350 `plan system --machine mega138k_ae350`（run `20261021T091304401142Z-plan-candidate-51bed1`）：

```text
machine-description REUSE 0fd408deed3a exact Board/Machine/vendor/tool input closure
cpu               REUSE 639cbe9e97f6 AE350 hard macro configuration; no soft CPU RTL
system            BLOCKED  P1 backend not implemented; no generated RTL or runnable system
```

`system BLOCKED` 是 [scripts/modular/machines.py:144-149](../scripts/modular/machines.py:144) 的既有 P1 状态，按原样记录为基线，不视为回归，重组各阶段不负责解除。AE350 `plan gateware`（run `20261021T090753510343Z-plan-candidate-425915`）可用：`machine-description REUSE 0fd408deed3a / cpu REUSE 639cbe9e97f6 / bootloader BUILD / gateware BUILD`。

## 2. host 测试现状清单（"现状 + 已知名单"）

明细：`build/reorg-stage0-hosttests.json`（64 项：`tests/*_test.py` 57 + `sim/` 7 个主机测试；单文件 300 s 上限）。汇总：**42 PASS / 16 FAIL / 6 SKIP**。

SKIP（板卡可达，本阶段规则禁跑）：`bios_ls_serial_test.py`、`linux_port_test.py`（串口会话）、`mini_operations_test.py`（驱动 programmer 命令）、`ae350_memory_test.py`、`firmware_verify_test.py`、`ae350_jtag_test.py`（board/session 消费者）。

FAIL 分类（无一例进入阶段判定，登记为现状）：

1. **driver 型 14 项**：脚本本身要求命令行注入 `--iverilog` / `--output-dir` / `--build-dir` / CPU 与镜像工件，不是独立可跑的 unittest，裸跑必然 argparse 退出——`cpu_copy_crc_test`、`ethernet_dma_verilog_test`、`ethernet_ring_ownership_test`、`ethernet_ring_verilog_test`、`lcd_backlight/memory/palette/skip_verilog_test`、`memory_controller_verilog_test`、`native_sd_verilog_test`、`shared_l2_verilog_test`、`usb_fs_phy_verilog_test`、`usb_host_enumeration_test`、`rtl_defaults_test`。且本机 **未安装 iverilog/vvp**（PATH 无此工具，`.tools.local.json` 亦无该角色）；这批测试在流内或配置 iverilog 后才可运行。
2. **modular_reuse_test（真红，HEAD 既有）**：source-replay 的受限 checkout 里跑 `mini.py _worker`，[scripts/mini.py:80](../scripts/mini.py:80) `from gateware.machines import ae350_map`（e3c59316 引入，ddr-suite 参数构建路径）在只含 hal 闭包（5 个 gateware 文件，无 `gateware/machines/`）的树里 `ModuleNotFoundError: No module named 'gateware.machines'`。日志：`build/work/modular-reuse-nfnau_8u/checkout/build/runs/20261021T092203943607Z-source-replay-d075da/hal-ninja.log`。与重组无关；修法（import 下沉或闭包补齐）不并入重组提交，待定。
3. **sim/test_features.py（断言过时）**：首行 `assert all(Features.resolve().as_dict().values())`；今日 `full` 默认 `fpu=False`（MMU/FPU 需配套 CPU RTL，已改显式开启），断言前提失效。登记为已知红，阶段 D 移文件时一并处置。

## 3. 20K PnR 时序基线

**最后板级合格参照**：`build/archives/v0.11.0-ed502601d172/full-rv32ima-rom8k-l28k/`（commit ed502601、validation setup 0/hold 0；Gowin 1.9.12.04_x64，与今日同一 `.tools.local.json` 路径同哈希；系统时钟依赖 Gowin 对 `CLKDIV/CLKOUT` 的隐式推断 16.667 ns，仓内 SDC 只 create_clock 了 clk27/eth_clocks_ref_clk/usb_ulpi_clk 三个外部时钟）。归档时序细节表最坏建立 slack 仅 **+0.031 ns**，整体 0 违例。

**5acca4d 树上 full-profile 重跑 = RED（已知红，缺陷域：gateware 时序回归，非重组所致）**。run `20261021T085039300309Z-build-reorg-stage0-baseline-20k-r2-b6a648`（同参数复现 v0.11.0：`--profile full --cpu-variant linux --cpu-verilog <归档 CPU VexRiscv_Mmu_s1m1.v> --feature fpu=off --feature mic_stereo=off --sd-profile lite --usb-backend ultra --audio-clock dds --boot-mode rom --rom-size 8192 --l2-size 8192 --place-option 2 --route-option 2`）：

- setup 违例 **757**、hold 0：**754** 端点集中在 `CLKDIV/CLKOUT.default_gen_clk`（系统时钟域，TNS **-566.983 ns**）+ **3** 端点 `usb_ulpi_clk`（TNS **-0.606 ns**）；Logic **18904/20736（91%）**。
- 判定：约束机制与 v0.11.0 完全一致（同一工具、同一隐式时钟、TA1132 两代同报），差异是 ed502601→HEAD 的 RTL 增量（`board_sense +190`、`io_map +164`、`gpio_events +39`、`board_io ±77` 等）吃掉了 +0.031 ns 的刀片余量后 congestion 恶化。属**既有设计时序回归**；修复（RTL 优化或约束策略）不在重组范围，待用户裁决。
- 证据注记：工作区按 5acca4d 新语义复用，后续 minimal 重建覆写了该 workspace 的 PnR 工件；本文数字取自覆写前对 workspace 报告的直接解析，同记录在记忆 82f87866 续记。

**5acca4d 树上 minimal-profile 全流程 = GREEN（本轮可得的绿色对照）**：store `gateware/030dc2c45ab442de2bf59d77ac69a9fa7d9359b53408ece91749421a17c44d09`，run 同 §1。setup 0 / hold 0；最坏建立 slack **+4.214 ns**（VexRiscv 译码→访存路径）；Logic **6284/20736**、BSRAM 13/46；`pnr_report` 契约（`scripts/modular/rtl.py`）直接可读。

**后果（阻塞项，已提请用户）**：计划规定阶段 A 的 20K 对比基线"必须是 5acca4d 树上 full-profile PnR 重跑产物（成功发布 store 后才登记）"，而该重跑在当前树无绿色产物；在用户裁决（替代门 = minimal 绿基线 + full 网表等价 + 阶段 E 末次全量重建；或先立项修时序）之前，阶段 A 不开。

## 4. AE350 时序基线

**采用基线**：`build/archives/ae350-board-io-7339ab23d5cb`（source_commit `7339ab23d5cb`、kind:code-snapshot、dirty:false）。时序：`build/store/gateware/44405c059cf8b49a5944652f0b676294242b06cd2e07931ca11028fc73033289/timing.json` = **setup 0 / hold 0 / min_pulse_width_slack_ns 0.142**；同目录含 `gateware/impl/pnr/{project_tr_content.html,project.tr,project.rpt.txt}`。采用前置条件本轮复核：`git diff 7339ab2..HEAD -- gateware firmware` **为空**（5acca4d 仅动构建脚本：workspace 复用 + license retry，不改 PnR 输入），判定成立、无需重跑。

**当前树交叉重建（补做，绿）**：run `20261021T090847491097Z-build-reorg-stage0-baseline-ae350-r1-e401ef` → gateware **f56527dc3d28**，`ae350.timing_report` = **setup 0 / hold 0 / min_pulse_width_slack_ns 0.142**，与归档基线逐项一致。AE350 时序基线就此成立（归档为准，交叉件佐证）。

对照（不作基线）：今日候选 `stable-workspace-full-71e169`（digest `8c46d5e0…`，PnR 完成未板级合格）。今日板级历史：`build/runs/20261020T080229355702Z-board-ae350-p3-suite/result.json` passed=true（fs `d6a32f5add…`，早于 63db62b/7339ab2 的 gateware 变更，仅背景）。`plan system` BLOCKED 原样保留（§1）。

## 5. 阶段 0 manifest

`build/reorg-manifest.json`（`build/` 不入库，工作产物）：生成器 `build/reorg_stage0_manifest.py`，基础提交 `5acca4d`。**280 条**：P 6 / A 52 / B2-B4 各 2 / C 47 / D 169；`unmapped=0`（gateware/firmware/scripts/sim/tests 全部跟踪文件已映射或显式 stay）；引用扫描 656 个文件（模板 `gateware/|firmware/|scripts/[a-z0-9_]+\.py|sim/|__file__|parents\[|os\.path\.join`，范围 `*.py,*.md,*.ps1,*.c,*.h,*.ld,SKILL.md,AGENTS.md`）；185 条至少一处现役引用。历史表 `docs/version-history.md`、`docs/lab-environment.md` 命中 9 对，登记为 register-only 不改写（`CHANGELOG.md` 无命中）。

与计划正文的两处偏差（按"以代码为准更新 manifest"规则记录）：

1. `scripts/tests/test_board_events.py` → `tests/common/`（正文把 `scripts/tests/` 五个全划 138K；该测试对象是两机共用的 gpio/WS2812 core，阶段 A 起就是 `devices/` 回归）。
2. `tests/linux_console_perf.py` → `scripts/primer20k_vexriscv/`（非 `_test` 模块，是 20K 性能驱动，按"20K 专用脚本"规则归位）。

待查证移交项：`firmware/examples/monitor.c:4` 的 `../../bootloader/image_abi.h` 无对应文件（今日仅 `image.h`），来源查明前不盲改（阶段 C 处理）。

## 6. 本阶段不做的项（登记）

20K full 时序回归修复；`mini.py:80` 闭包 import 下沉；`sim/test_features.py` 过时断言更新；iverilog 环境角色配置；AE350 `system` P1 解除。均不并入重组各阶段提交。
