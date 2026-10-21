# 多平台目录重组与设备解耦执行计划

日期：2026-10-21。修订四（同日，用户裁决）：20K full-profile PnR 完全退出阶段 0/A–D 的验证门，full 全量重建只在阶段 E 验收跑一次并登记结果（预期红，时序回归作为独立缺陷另行立项）；细则见 §3 的"裁决（修订四）"。修订三（同日）：gemini-4-argon 第二轮复核（对修订二，结论 STILL-BLOCKED：3 个新阻塞 B-R2-1/2/3 + 每阶段 majors）经 lead 逐行验证后落进本修订，落点见 §8。修订二文本（含第一轮落点 §6、第二轮落点 §7）保留。本文把 [多平台组织提案](multi-platform-architecture-proposal.md)（下称"设计文档"）的 §3/§4 落为可分批执行的计划。设计文档的兼容性判定（§6）和 requires/provides（§7）不在本计划内。

硬规则（贯穿每一阶段，违反则该提交不算完成）：

1. **同提交改引用。** 一次提交移动或改名的每个路径，必须在同一次提交里改完所有 import、字符串路径、`__file__`/`parents` 相对路径、闭包声明和仍在使用的操作文档命令。禁止"先搬、下阶段再改引用"。
2. **闭包是文件允许列表，不是目录 glob。** 只列入该机器 import 图实际到达的文件。声明路径不存在、为空、或实际 import 超出声明，构建失败。禁止 `glob`/`rglob` 静默收集（现状 [scripts/modular/ae350.py:32-41](../scripts/modular/ae350.py:32) 就是这个缺陷）。
3. **闭包声明只写该提交之后存在的路径。** 脚本包还没拆时，不要把 `scripts/modular/machines/<id>/` 写进闭包。脚本在阶段 D 移动时，同一次提交改声明。
4. **没有第二个消费者就不建共享目录。** `interconnect/`、`transport/`、`cpu/`、`gateware/memory/` 不预建。`devices/` 只收已经或本阶段真正被两台机器共用、或已从机器 adapter 拆出的功能核心。

## 0. 已拍板，本修订不推翻

1. **范围**：搬迁 + 设备解耦。只移动文件、或加原样转发层，不算完成。
2. **缓存**：接受一次性全量重建。各机器完整重建是验证门，不保留旧 key。
3. **仿真**：`sim/` 并入 `tests/`，不再保留第三个测试目录。
4. **旧回执**：`build/runs/` 里记录的测试路径不再复核。[scripts/ae350_report.py:169-170](../scripts/ae350_report.py:169) 的 expected 集合直接改新路径，不写双兼容。这**不**包括删除 [scripts/modular/ae350.py:145-165](../scripts/modular/ae350.py:145) 的增量 PnR 种子守卫：那是在读已发布构件里的 `reference_device`/`reference_part`，不是回执路径。搬迁后旧构件对不上新 staged 路径就拒绝做种子、退回 full PnR，守卫本身保留并改到新路径。
5. **脚本**：20K 专用验证脚本移入 `scripts/primer20k_vexriscv/`。`boot_upload.py` / `boot_image.py` 里被 AE350 引用的部分先抽到公共层，不能整文件划给 20K。

已定：目录名 = `--machine` ID 全名（[scripts/mini.py:33](../scripts/mini.py:33)）。docs/ 保持扁平（[scripts/build_cleanup.py:75](../scripts/build_cleanup.py:75) 的 `docs/*ae350*.md` 依赖扁平）。未显式 `--machine` 的旧命令仍只指 20K。

## 1. 目标树（终态）

```text
gateware/
  boards/
    primer20k_dock3713/board.json          # ← gateware/board.json
    mega138k_pro/{board.json, io_map.py}   # io_map ← gateware/io_map.py
                                           # 移动时同提交改 io_map.py:6 parents[1]、:7-8 BOARD_PATH/MACHINE_PATH
                                           # 和 scripts/modular/machines.py:75 的 machines/mega138k_ae350.json
  devices/                                 # 只放已拆出的功能核心，不放机器 CSR/引脚
    gpio/core.py                           # ← gpio_events.ButtonEvents；两机今日已共用（board_io.py:3-4、
                                           #   ae350_board_io.py:13-14），阶段 A 就移入，不留到 B
    ws2812/{core.py,timing}                # ← ws2812.WS2812Core 与 timing（test_board_events.py:17-18 同时引用），
                                           #   同样阶段 A 移入
    audio/{core,registers,assemble}.py     # 阶段 B3 从 20K peripherals/ 拆出
    microphone/{core,registers}.py         # 阶段 B2 从 20K peripherals/ 拆出
    video/{core,rgb_lcd}.py                # 阶段 B4；HDMI adapter 不在本计划
  machines/
    primer20k_vexriscv/
      __init__.py
      {soc,rtl,config,features,constraints,csr_layout,memory_map,bus,flash_xip,ddr_boot}.py
      memory/{ports,native_dma,shared_l2,gw2_ddr}.py
                                           # ports←memory.py，gw2_ddr←ddr.py，shared_l2←shared_l2.py
                                           # native_dma.py 必须登记（memory.py:114 与 vendor/sddma.py:15 import，
                                           #   另有 sim/test_native_dma.py、tests/{native_sd,ethernet_dma,ethernet_ring}_verilog_test.py）
                                           # 仅 20K 消费，不升到 gateware/memory/
      peripherals/{board_io,audio,audio_clock,microphone,video,sd,sd_dma,ethernet,ethernet_ring,ethernet_dma,usb,usb_ultra,sddma}.py
                                           # audio_clock.py 今日只有 20K 引用（soc.py:32、sim audio/mic 测试），先留 peripherals/；
                                           # B3 决定是否随 audio core 进 devices/audio
                                           # sddma.py ← vendor/sddma.py：它 import 20K 的 sd_dma/native_dma/memory（vendor/sddma.py:14-16），不是纯 vendor
                                           # usb_ultra.py 今日用 Path(__file__).parent/'vendor'（usb_ultra.py:113-117）；进 peripherals/ 后必须改成显式 vendor 根，不能靠 parent
                                           # 阶段 A 的完整归属清单以 build/reorg-manifest.json 为准，正文不称全量
    mega138k_ae350/
      __init__.py, machine.json            # ← mega138k_ae350.json
      soc.py          ← mega138k_ae350_ddr.py
      platform.py     ← ae350_platform.py
      clock.py        ← ae350_clock.py
      cpu.py          ← ae350_native.py    # 无第二 CPU 消费者前不建 gateware/cpu/
      interconnect.py ← ae350_interconnect.py
      address_map.py  ← ae350_map.py
      ddr3_ahb.py                          # 仅本机；相对 vendor 路径（mega138k_ae350_ddr.py 与 ddr_inputs.py:14）同提交改掉
      peripherals/board_io.py    ← ae350_board_io.py
      peripherals/board_sense.py ← gateware/board_sense.py
  vendor/
    README.md
    gowin_gw2a/gw2ddrphy.py                # 仅 20K 闭包
    gowin_gw5ast/{ae350_soc/, gowin_ddr3_400/}   # 仅 138K 闭包
    litesdcard/{sdcore.py, LICENSE.litedram}    # sdcore.py 不 import gateware，可共享；sddma 不在这里
    usb_fs_phy/ ; usb_host/                # 保持相对深度，避免 usb_fs_phy_verilog_test.py 的 vendor 路径无故变化；若 usb_ultra 改显式根，测试路径同提交更新

firmware/
  bootloader/
    include/image.h                        # ← bootloader/image.h；移动后 firmware.py:214、252 的 includes 增加
                                           #   firmware/bootloader/include（bootloader main.c 是裸 #include "image.h"）
    primer20k_vexriscv/                    # ← bootloader/{main,ddr,flash,flash_xip}.c, boot.ld, xip.ld
    mega138k_ae350/                        # ← bootloader/ae350/
  bsp/
    primer20k_vexriscv/                    # ← boot/start.S, bootloader/app.ld
    mega138k_ae350/
      runtime/  ← bsp/mega138k_ae350_payload/{start.S,link.ld,trap.c,irq.c,irq.h}
      dma/      ← firmware/ae350/dma/
      io/       ← bsp/mega138k_ae350_payload/{gpio,board_io,board_sense}.*
                 # 拆分受 ae350.py:330-337 平铺收集约束，见阶段 C；保持单层 runtime/ 也是合法选项
  hal/ drivers/ common/ libc/ bios/ apps/  # 本次不动；apps 的 ../../bios/include/bios.h 因此不受影响
  diagnostics/
    primer20k_vexriscv/                    # ← diagnostics/tests.{c,h}
    mega138k_ae350/                        # ← diagnostics/ae350_memory/ + examples/ae350_{dma,io}_suite.c
  examples/
    bios_payload/                          # bios_demo.c, nyancat_demo.c
    primer20k_vexriscv/                    # 其余 11 个跟踪 .c：hal_demo、sd_demo、audio_demo、ethernet_demo、
                                           #   usb_input_demo、microphone_demo、microphone_stereo_demo、monitor、
                                           #   libc_test、boot_memory_test（nyancat/bios_demo 归 bios_payload）
    mega138k_ae350/                        # ae350_payload_demo.c, ae350_led_rgb_demo.c
  tools/primer20k_vexriscv/flash_readback.c
  payload/                                 # 空且未跟踪，删除
  opensbi/ uboot/ linux/ vendor/           # 不动

scripts/
  mini.py                                  # 唯一入口。--machine choices 读注册表，不再手写二元组；
                                           # mini.py:151 的 from mini_ops.board import perform 改按机器分发（阶段 D）
  modular/                                 # 只留平台无关框架
    compile.py                             # 阶段 P 抽出：BASE_FLAGS, ROLES, ninja_build, source_files。
                                           # 不含 generated_headers：它体内调 generate_csr(20K rtl) 与
                                           # gateware.features/memory_map（firmware.py:5-8），进共享 compile.py 等于
                                           # 把 20K gateware 拖进 AE350 import 闭包（B-R2-1）。留在 20K 后端，
                                           # 同提交删 system.py:12 对它的死 import（全文件只有 :12 一处出现）
    cli.py targets.py storage.py workspace.py environment.py executor.py worker.py gowin.py replay.py
                                           # 框架不得静态 import 机器后端。完整清单（B-R2-2，全部 5acca4d 行号核实）：
                                           # cli.py:8（from .system import import_legacy,build_firmware_system,
                                           #   export_bundle,verify_export,import_export,resolve_system,materialize，
                                           #   顶层 import，每次 CLI 调用即崩）、cli.py:22/71/138（mini_ops.release 的
                                           #   add_arguments/perform/verify_bundle）、cli.py:158（.contracts）；
                                           # targets.py:5（from . import arguments,configuration）、:81/:146/:189（.system）、
                                           #   :102-103；worker.py:18-19（system.import_export + firmware.build_component）、
                                           #   :30；replay.py:7-9、:63（.system.export_bundle）、:64；mini.py:138-156
                                           #   （ae350 ops 六个 import + :151 board.perform + :156 doctor）；
                                           # prepare_commit.py:64（ae350 timing_report）、:152/:167（mini_ops.release）；
                                           # build_records.py:118（.system.materialize，进 tools/ 后同规则）。
                                           # arguments.payload()（arguments.py:51-59，--source/--include-dir/--cflags
                                           #   等两机共用的 payload 参数注册）不随 20K 后端走：阶段 D 抽进框架
                                           #   （targets.py 或 machines/__init__.py），否则 AE350 build payload 断。
                                           # scripts/builders/ 的 cpu_qualification.py:24 与 opensbi.py:6 import
                                           #   scripts.modular.rtl（pnr_report/generate_csr）：builders 非框架，
                                           #   阶段 D 同提交改成 20K 包新路径即可。
                                           # 由阶段 D 改注册表分发；在那之前不得把这些文件划进任何机器包
    machines/
      __init__.py                          # 注册表。未知 id 显式报错，禁止落入 20K（今日 targets.py:66-67 会静默落入）
      primer20k_vexriscv/                  # ← modular 的 20K 后端整组：hardware.py、firmware.py（compile.py 抽走后的剩余）、
                                           #   rtl.py、cpu.py、contracts.py、configuration.py、system.py、arguments.py。
                                           #   它们本就是 20K 后端：arguments.py:3-4 import gateware 特性、
                                           #   system.py:7 import abi_tag。system.py:90 哈希 Path(__file__).parent 下
                                           #   (system.py, cpu.py, contracts.py)——三者必须同目录一起移动。
      mega138k_ae350/                      # ← ae350.py, ddr_inputs.py，以及 machines.py 的 AE350 解析
  mini_ops/
    catalog.py                             # 两机共用
    common.py                              # 阶段 P 抽出：Operation（原 board.py:21；被 ae350.py:9、ae350_boot_ops.py:112、
                                           #   ae350_ddr_suite.py:6、ae350_dma_suite.py:10、ae350_io_suite.py:10、
                                           #   ae350_jtag_test.py:10、ae350_upload.py:12、mini.py:151、
                                           #   tests/mini_operations_test.py:18 import）
    primer20k_vexriscv/                    # ← board.py, linux.py, probe.py, artifacts.py, release.py
                                           #   artifacts/release 的消费方全是 20K 侧（boot_upload.py:121、board.py:16、
                                           #   mini_operations_test.py:17；release.py 走 firmware_verify/benchmark）
    mega138k_ae350/                        # ← ae350_*.py + ae350_image.py + ae350_report.py
  builders/                                # 保持原位
  primer20k_vexriscv/                      # 只放 20K 专用脚本（见阶段 P 之后的剩余物）
  tools/                                   # 机器无关：阶段 P 的 boot_session.py、boot_image.py（纯 framing）、
                                           #   uart_state.py（STATE= 协议，机器无关，AE350 经 BootSession 使用）、
                                           #   programmer_cable.py（ae350_session.py:14、tests/ae350_memory_test.py:27、
                                           #   tests/programmer_cable_test.py:14、boot_upload.py:13、board.py:18 消费），
                                           #   加 doctor/prepare_commit/build_* 等
  env.ps1 bootstrap.ps1 keep_awake.ps1 README.md

tests/
  common/                                  # 跨机器与构建系统测试，含 machine_build_test.py、workspace_build_test.py
  primer20k_vexriscv/                      # 20K 主机测试
    sim/                                   # 只放今日调用 migen run_simulation 的 13 个 sim 文件；
                                           #   sim/ 没有 iverilog 文件，iverilog 测试本就在 tests/*_verilog_test.py
  mega138k_ae350/                          # ← tests/ae350_* + scripts/tests/（5 个）
```

`sim/` 现有 git 跟踪 22 个文件：20 个 `test_*.py` + `generate_usb_pio_bus.py` + `README.md`。不是"其余 19 个"。`generate_usb_pio_bus.py` 进 `scripts/primer20k_vexriscv/`（或该机测试辅助目录，阶段 0 manifest 定一处）。`README.md` 跟仿真测试走，[docs/bootloader.md](bootloader.md) 里对 `sim/README.md` 的链接同提交改。主机测试不放进 `sim/` 子目录——22 个跟踪文件里不 import `run_simulation` 的有 **7** 个：`test_boot_image.py`、`test_boot_session.py`、`test_build_report.py`、`test_programmer_result.py`、`test_audio_capture.py`、`test_features.py`、`test_rtl.py`。`.py` 与同名 `.c`/`.S`/`.v` 作为一组移动。

分类规则：被另一台机器 import 的符号先抽到 `scripts/modular/` 或 `scripts/tools/`，再移动剩余文件。阶段 0 manifest 是分类的唯一清单；正文里的树是目标形状，执行以 manifest 为准。

## 2. 闭包协议

替换对象：

- [scripts/modular/hardware.py:14-18](../scripts/modular/hardware.py:14) 的 `gateware/` 全目录加黑名单。
- [scripts/modular/ae350.py:17-41](../scripts/modular/ae350.py:17) 的 `scripts/modular/*.py`、`gateware/*.py`、`gateware/vendor/**` glob。

协议：

- 每台机器导出 `source_closure(root) -> {repo-relative path: role}`，角色只区分 `source` 和 `external`。
- 加载时逐项 `is_file()`，缺文件或声明目录为空即失败。不用 glob 发现输入。
- 另有一条 host 测试：用 ast 扫该机器入口的 import 闭包，声明集合必须是它的超集。漏声明失败，而不是把整个 `gateware/` 收进来。
- **阶段 A 的 20K 声明**包含它当时实际 import 的 `machines/primer20k_vexriscv/**`（含 `memory/native_dma.py` 与 `peripherals/` 全部，audio/audio_clock/microphone/video 此时仍是机器文件）、`boards/primer20k_dock3713/board.json`（hardware.py:16 今日锁 `gateware/board.json`；machine_build_test 断言改 board.json 失效 20K——漏声明则隔离门假绿）、`devices/gpio`、`devices/ws2812`（阶段 A 随 gateware 一并移入——两机今日共用，见 §1）、`vendor/gowin_gw2a`、`vendor/litesdcard/sdcore.py`、`vendor/usb_*`，以及当时还留在 `scripts/modular/` 的 20K 后端文件。**不**包含 `ddr3_ahb.py`、`board_sense.py`、`io_map.py`、`vendor/gowin_gw5ast/`，也**不**包含 `devices/{audio,microphone,video}`——它们 B2-B4 才存在，拆出 core 的提交同一次把目录加进声明。
- **阶段 A 的 138K 声明**包含 `boards/mega138k_pro/**`、`machines/mega138k_ae350/**`、`devices/gpio`、`devices/ws2812`、`vendor/gowin_gw5ast/**`，以及外部 vendor 工程。**不**包含 `devices/{audio,microphone,video}`、20K `memory/`、`shared_l2`、`vendor/gowin_gw2a`、`vendor/usb_*`。今天 AE350 RTL 不引用 audio（gateware/machines/{mega138k_ae350.py,mega138k_ae350_ddr.py,ae350_map.py} 的 import 图已核）。
- 外部输入显式列入，不假装它在仓内：默认路径是 [scripts/modular/machines.py:99](../scripts/modular/machines.py:99) 的 `store.root.parent.parent / 'TangPrimer-138K/example/ae350_customized_demo'`。缺文件维持现有的响亮失败。搬迁不改这个仓外树。
- 新文件未出现在任一机器声明里，且被某机器 import：构建失败。没有 import 的新文件不自动进入任何 key。

隔离不变式（阶段 A 起就应成立，阶段 D 后再测一次）：改 `io_map.py` 或 `board_sense.py` 只失效 138K；改 20K `audio.py` 或 `ddr.py` 只失效 20K。

## 3. 阶段

每阶段一次提交，遵守 pre-commit SOP。验证门必须在该提交的树上为绿。不把"下阶段会修"算进本阶段的门。

### 裁决（修订四，2026-10-21 晚，用户拍板）

20K full-profile PnR 时序在当前树为红（既有 RTL 增量回归，见 `reports/reorg-stage0-baselines.md` §3），计划原要求的"绿色 full 重跑基线"不可得。用户裁决：**full 完全退出各阶段门**。

- 阶段 0 的 20K 基线即按 `reports/reorg-stage0-baselines.md`（提交 `aec1fd1`）判定为记录完成：v0.11.0 = 最后板级合格参照；5acca4d full 红指纹（754 CLKDIV 域 + 3 usb_ulpi 端点，hold 0）= 缺陷登记；minimal 全流程绿锚点（0/0，最坏 +4.214 ns，store `030dc2c45ab4`）= 每阶段时序哨兵。
- 阶段 A–D 各门不含 20K full 重建或 full 对比。20K 侧时序哨兵 = minimal 全流程重建 0/0、hold 恒 0、不新增违例域；host 回归 = 对"现状 + 已知名单"不新增红；隔离反证照常。
- **AE350 gateware 重建 + 时序对比（对 0/0/0.142 绿基线）保留为各阶段门**：裁决只豁免 20K 的 red full-profile 组合，AE350 是该机唯一绿色时序信号，不得豁免。
- 阶段 E 验收跑一次两机全量重建：20K full 结果原样登记（预期仍红，除非缺陷已被另行修复），AE350 full gateware 必须保持 0/0/0.142。
- 时序回归修复不在重组范围：独立立项，用户排期。

### 阶段 0 — 基线与 manifest

不改源码布局。

- 记录两机 `mini.py plan` 的全部目标。AE350 `plan system` 今日就是 BLOCKED（[scripts/modular/machines.py:144-149](../scripts/modular/machines.py:144)），原样记为基线，不当成回归。
- 记录 host 测试通过清单、20K full PnR 时序，以及当前 AE350 gateware 的时序报告路径。两机基线实测落点（本修订在 5acca4d 工作机上核实）：
  - 20K：`build/refs/current.json` → `build/archives/v0.11.0-ed502601d172/full-rv32ima-rom8k-l28k/`。身份已核（build-info.json：commit ed502601d17220c41ca05140d832df23204a3d70、dirty:false、kind:captured、2026-10-14；validation.json：setup 0/hold 0；报告在其 `reports/pnr/pnr/{project_tr_content.html,project.tr.html,project.rpt.txt}`）。但 `git diff --stat ed502601d172..HEAD -- gateware firmware` = **64 files +54552/-42**，归档不是 RTL-当前的。登记为"最后板级合格参照"。原文要求"阶段 A 的对比基线必须是 5acca4d 树上的 full-profile PnR 重跑产物（成功发布 store 后才登记）；重跑失败则修复前阶段 A 不得开"——该重跑在 PnR 阶段红（既有 RTL 增量回归），整条要求由修订四裁决废止：full 退出各阶段门，阶段 0 基线以 `reports/reorg-stage0-baselines.md`（提交 `aec1fd1`）判定为记录完成。
  - AE350：最新提交级归档 `build/archives/ae350-board-io-7339ab23d5cb`（source_commit 7339ab23d5cbd14b9586e7542b11cf5abef9e482、dirty:false、kind:code-snapshot）。前置条件按修订三收窄为 `git diff 7339ab2..HEAD -- gateware firmware` 为空——**实测为空，判定可采用、无需重跑**。`scripts/modular/ae350.py` 不再进该 diff 条件：5acca4d 对它的 +128/-28 已逐段核验——`rtl()` 对默认 full 模式不改任何 PnR 输入（DDRSoC 生成、.cst/.sdc/run.tcl、IP 树、Gowin 选项不变），只加 workspace 复用、incremental-seed 管线（full 模式下不激活）与 license retry；`firmware()` 把 BOOT_BUILD_ID 宏移进 gen/identity.h，只影响 ITCM ROM 里的 12 字符横幅串。时序证据：`build/archives/ae350-board-io-7339ab23d5cb/build/store/gateware/44405c059cf8b49a5944652f0b676294242b06cd2e07931ca11028fc73033289/timing.json`（setup 0、hold 0、min_pulse_width_slack_ns 0.142）与同目录 `gateware/impl/pnr/{project_tr_content.html,project.tr,project.rpt.txt}`。今日候选 `stable-workspace-full-71e169`（digest 8c46d5e0…，PnR-not-board-qualified）只作对照，不作基线。A 的对比门不能对着空基线。
  - 外部评审给出的 `build/store/gateware/ed502601d172/`、`build/refs/ae350-current.json`、`reports/ae350-m6-acceptance.md` 均不存在（是归档内相对路径漏了 `build/archives/<name>/` 前缀），禁止引用。
- `sim/test_build_report.py:8` 的 `from scripts.build import pnr_report` 在 5acca4d 即坏（`scripts/build.py` 不存在，`pnr_report` 在 `scripts/modular/rtl.py`）。基线清单必须是"现状 + 已知名单"：在本阶段记录之前先开一个独立小提交修复该 import 并复跑，之后基线才是全绿现状。
- 生成 `build/reorg-manifest.json`（`build/` 不入库）。字段：`old`、`new`、`phase`、`references[]`。引用扫描模板：

  `gateware/|firmware/|scripts/[a-z0-9_]+\.py|sim/|__file__|parents\[|os\.path\.join`

  范围：`*.py`、`*.md`、`*.ps1`、`*.c`、`*.h`、`*.ld`、`SKILL.md`。`CHANGELOG.md`、`docs/version-history.md`、`docs/lab-environment.md` 里的历史版本表只登记、不改写。
- manifest 是 A–D 的打勾基线。计划正文与 manifest 冲突时，以代码为准更新 manifest，不反过来猜。阶段 A/C/D 正文点名的清单都是"必须项"，manifest 负责把它们补全为全量。

### 阶段 P — 先抽共享符号，再允许按机器搬脚本

本阶段不搬目录，只消除跨机 import。验证门：现有 host 测试全绿，两机 `plan` 与阶段 0 一致（key 可以因新文件而变，行为不变）。

必须抽走、不能留在即将变成 20K 包的文件里：

| 符号 | 今日位置 | 去向 | 跨机/框架引用 |
| --- | --- | --- | --- |
| `BootSession`、`programmer_succeeded` | `scripts/boot_upload.py` | `scripts/tools/boot_session.py` | [ae350_session.py:13](../scripts/mini_ops/ae350_session.py:13)、[ae350_upload.py:15](../scripts/mini_ops/ae350_upload.py:15)、[ae350_ddr_suite.py:79](../scripts/mini_ops/ae350_ddr_suite.py:79)、[ae350_dma_suite.py:44](../scripts/mini_ops/ae350_dma_suite.py:44)、[ae350_io_suite.py:45](../scripts/mini_ops/ae350_io_suite.py:45)、[ae350_boot_ops.py:113](../scripts/mini_ops/ae350_boot_ops.py:113)，以及 20K 的 `board.py`、`firmware_verify.py` 等 |
| `wait_for` 及 STATE= 协议（整模块 `uart_state.py`，机器无关；`BootSession.wait_state` 在 boot_upload.py:50 内部 import 它） | `scripts/uart_state.py` | `scripts/tools/uart_state.py` | boot_upload.py:50；AE350 经 `BootSession` 使用 |
| `resolve_location`（整模块 `programmer_cable.py`） | `scripts/programmer_cable.py` | `scripts/tools/programmer_cable.py` | [ae350_session.py:14](../scripts/mini_ops/ae350_session.py:14)、[tests/ae350_memory_test.py:27](../tests/ae350_memory_test.py:27)、tests/programmer_cable_test.py:14、boot_upload.py:13、mini_ops/board.py:18 |
| `Operation`（`board.py:21`） | `scripts/mini_ops/board.py` | `scripts/mini_ops/common.py` | ae350.py:9、ae350_boot_ops.py:112、ae350_ddr_suite.py:6、ae350_dma_suite.py:10、ae350_io_suite.py:10、ae350_jtag_test.py:10、ae350_upload.py:12、mini.py:151（perform）、tests/mini_operations_test.py:18 |
| 纯 framing：`MAGIC`、`VERSION`、`HEADER`、`LOAD`、`FLASH_OFFSET`、`FLASH_SIZE`、`MAX_PAYLOAD`、`CHUNK`、`packet`、`pack_image`、`unpack_image` | `scripts/boot_image.py` | `scripts/tools/boot_image.py` | [ae350_image.py:11](../scripts/ae350_image.py:11)、[ae350_upload.py:130](../scripts/mini_ops/ae350_upload.py:130)、[artifacts.py:9](../scripts/mini_ops/artifacts.py:9)、[probe.py:6](../scripts/mini_ops/probe.py:6)、system.py:7、hardware.py:69 |
| `abi_tag`（20K 语义：vexriscv-lite/rgb_lcd/l2/usb_ultra；不 import gateware，只有 20K 侧用） | `scripts/boot_image.py` | **不抽公共层**：留在剩余的 `scripts/boot_image.py`（20K 包，阶段 D 随文件移动） | 阶段 P 同提交把 hardware.py:69-70、system.py:7、artifacts.py:9、sim/test_boot_image.py:8、sim/test_features.py:6、tests/config_test.py:34 的 import 拆成 framing（tools）+ abi_tag（boot_image 剩余文件）两路 |
| `BASE_FLAGS`、`ROLES`、`ninja_build`、`source_files` | `scripts/modular/firmware.py` | `scripts/modular/compile.py` | [ae350.py:10](../scripts/modular/ae350.py:10)、[cli.py:94](../scripts/modular/cli.py:94)、[hardware.py:9](../scripts/modular/hardware.py:9)、worker.py:19、system.py:12、replay.py:7 |
| （B-R2-1 附带动作） | `scripts/modular/system.py:12` | `generated_headers` 在 system.py 全文件只出现在 :12 的 import 上，是从未调用的死 import；阶段 P 同提交删掉。`generated_headers` 本体留 20K 后端（它调 generate_csr + gateware.features/memory_map），不进 compile.py | system.py:12 |

`boot_upload.py` 里 20K 专用的 `program()`、菜单和 Flash 布局留在原文件，阶段 D 再移入 `scripts/primer20k_vexriscv/`。`firmware.py` 里的 `build_component` 仍是 20K 后端，阶段 D 进机器包。阶段 P 之后的准确状态：**AE350 侧脚本与 mini_ops 不再 import 20K 根脚本**；框架对 20K 后端的静态 import（worker.py:19、30，replay.py:7-9，targets.py:5、102-103，mini.py:151，prepare_commit.py:64 取 ae350 `timing_report`）此时仍存在，由阶段 D 的注册表分发消除——在那之前这些框架文件不得被划进任何机器包，也不得宣称"P 之后框架不再 import 机器包"。

### 阶段 A — gateware 搬迁 + 闭包替换

按 §1 移动 gateware。`memory.py` / `ddr.py` / `shared_l2.py` / `native_dma.py` 在这一步进入 `machines/primer20k_vexriscv/memory/`，不另开 B0 再搬一次。`ddr3_ahb.py` 留在 `machines/mega138k_ae350/`。gpio/ws2812 两机共用，本阶段就建 `devices/{gpio,ws2812}` 并移入（原 B1 并入本阶段）；`audio.py`、`audio_clock.py`、`microphone.py`、`video.py` 本阶段作为 20K 机器文件进 `peripherals/`，B2-B4 才拆 core 进 `devices/`。

同一次提交必须改完：

- 全部 `from gateware...` import。除计划初稿点名的 builders 与 modular 文件外，还包括 [scripts/mini.py:80](../scripts/mini.py:80)、[scripts/modular/ae350.py](../scripts/modular/ae350.py)、[scripts/modular/ddr_inputs.py:10](../scripts/modular/ddr_inputs.py:10)、[scripts/modular/system.py:301](../scripts/modular/system.py:301)、`scripts/modular/{arguments,configuration,contracts,rtl}.py`（arguments.py:3-4、configuration.py:3-4、contracts.py:3、rtl.py:6,15,36,76）、`scripts/builders/{cpu_qualification,matrix}.py`、`scripts/boot_image.py:5`、`scripts/generate_System.py`、`scripts/mini_ops/ae350_*.py`、[scripts/ae350_image.py](../scripts/ae350_image.py)、`scripts/tests/*`、`tests/*`、`sim/*`，以及 gateware 内部 import（`gateware/board_io.py:3-4`、`gateware/machines/ae350_board_io.py:13-14`、`gateware/machines/mega138k_ae350_ddr.py` 的 io_map 引用等）。`native_dma` 的六个 import 方（memory.py:114、vendor/sddma.py:15、sim/test_native_dma.py、tests/{native_sd,ethernet_dma,ethernet_ring}_verilog_test.py）同提交改。
- 字符串与相对路径：[firmware.py:173-175](../scripts/modular/firmware.py:173) 的 gateware 条目（bootloader 是 ROM gateware 的依赖，[hardware.py:91](../scripts/modular/hardware.py:91)，漏改则 A 的重建门必红）、[usb_ultra.py:113-117](../gateware/usb_ultra.py:113)、[soc.py:250](../gateware/soc.py:250) 的 `with_name('board.json')`（改指 `boards/primer20k_dock3713/board.json`）、[soc.py:101](../gateware/soc.py:101) 的 `parents[1]/build/cpu-features` 与 [usb.py:123](../gateware/usb.py:123) 的 `parents[1]/build/vendor`（目录深度从 1 变 3/4）、`io_map.py:6-8` 的 `parents[1]`/`BOARD_PATH`/`MACHINE_PATH` 与 [machines.py:75](../scripts/modular/machines.py:75) 的 `gateware/machines/mega138k_ae350.json`（改 `machines/mega138k_ae350/machine.json`，tests/machine_build_test.py:28、155 同步）、DDR IP 相对路径、[ddr_inputs.py:14](../scripts/modular/ddr_inputs.py:14)、[ae350.py](../scripts/modular/ae350.py) 里的 `gateware/vendor/ae350_soc`、[tests/machine_build_test.py:27-28](../tests/machine_build_test.py:27) 与同文件 232、254 行、[tests/workspace_build_test.py](../tests/workspace_build_test.py)、[tests/ae350_memory_test.py](../tests/ae350_memory_test.py) 里的闭包成员断言。本阶段就动、不留到 D 的 ROOT 字符串：tests/usb_fs_phy_verilog_test.py:21（`ROOT/'gateware/usb_fs_phy.v'`）、tests/cpu_copy_crc_test.py:37（`ROOT/'gateware/VexRiscv_full.v'`）——它们引用的是阶段 A 移动的 gateware 文件。
- `tests/machine_build_test.py:231-257` 的最小临时 checkout（只拷 8 个文件的 `self.root`）与严格闭包冲突：`source_closure` 逐项 `is_file()` 后，该测试的 `generate_soc` 会因临时树缺声明文件而失败。同提交让该测试补齐它声明需要的闭包文件（在临时树里造占位文件也算），不许为此把闭包放松成"缺文件跳过"。
- 用 §2 替换两套旧闭包。声明用阶段 A 之后的路径（含 `devices/gpio`、`devices/ws2812`；不含 `devices/{audio,microphone,video}`），此时脚本仍在 `scripts/modular/`。
- 重写 `incremental_seed` 读取的 staged board 路径。读不到旧构件的新路径就拒绝种子，不删除守卫：守卫对已发布旧构件读 `inputs/source/gateware/boards/mega138k_pro/board.json`，旧种子构件里只有 `gateware/board.json`，直接换路径会 `FileNotFoundError` 炸掉增量路径——按"路径不存在 → return None → 退回 full PnR"实现（决策 4：守卫保留）。
- [targets.py:61-67](../scripts/modular/targets.py:61) 改为注册表分发：未知 `--machine` 报错，不落入 20K。加反证测试：假机器 id 的 `plan` 必须失败。`mini.py:33` 的 choices 从注册表生成。

**验证门**（按修订四）：20K gateware 用 minimal 全流程完整重建，对阶段 0 绿锚点（0/0、最坏 +4.214 ns）对比时序；AE350 gateware 完整重建并对 0/0/0.142 基线对比；full-profile 组合不进本门；20K bootloader（rom 与 xip）和 AE350 bootloader/payload 能构建（证明 firmware.py 的 gateware 字符串已跟上）；host 回归对"现状 + 已知名单"不新增红；隔离反证（改 `io_map.py` 只失效 138K）。本阶段不要求固件目录已经重组。

### 阶段 B — 设备解耦

只拆已有行为、且拆完后机器 adapter 变薄的设备。每小步同提交更新闭包允许列表。外部寄存器/引脚行为不变。实板验收在 B 全部完成后合并一次，不在每个小步上板。

- B1（gpio/ws2812）已并入阶段 A：两机 import 属实，阶段 A 直接进 `devices/`。回归 [scripts/tests/test_board_events.py](../scripts/tests/test_board_events.py) 与 [sim/test_board_io.py](../sim/test_board_io.py) 在阶段 A 门上跑。
- B2 microphone：拆 I2S/snapshot/FIFO 与 CSR。overflow 语义在拆分时固定（丢弃或中止，写进 core 的接口说明，不另开文档工程）。回归 [sim/test_microphone.py](../sim/test_microphone.py)。拆出后同提交把 `devices/microphone` 加进 20K 闭包声明。
- B3 audio：拆 PT8211、FIFO、ring reader、地址检查与 CSR。`AudioDDS`（audio_clock.py）跟着 20K audio core 走，进不进 `devices/audio/clock.py` 在本步定并写进提交说明；不放进 138K 闭包。回归 [sim/test_audio.py](../sim/test_audio.py) 与 [sim/test_audio_reference.py](../sim/test_audio_reference.py)。[tests/csr_packing_test.py](../tests/csr_packing_test.py) 只覆盖 `BoardIO` 打包，**不能**当作 audio 回归。拆出后同提交更新闭包声明。
- B4 video：按已有 LCDScan/PalettePixels 切控制、扫描、reader、CDC。输出层留在 20K rgb_lcd adapter。回归 `tests/lcd_*_verilog_test.py`。拆出后同提交更新闭包声明。
- 不建 `gateware/memory/`。两套 DDR 不是同一模块的两个消费者。
- `cpu/`、`transport/`、`interconnect/` 仍不建，除非本阶段真的出现第二个 import 方。出现了才加目录，并在该提交的说明里指出第二个消费者。

每小步的门：该 core 的接口测试、所属机器的仿真、该机 PnR。修订四注：B 的 core 不在 minimal 组合内，其"该机 PnR"按裁决降级为 full-profile RTL 生成（无 PnR）+ 仿真回归作为每小步门；full 时序只在阶段 E 末次重建登记。B 末实板：20K audio 播放/录音、video ring、io；AE350 io-suite 与 dma-suite。

### 阶段 C — firmware 重组

按 §1 移动固件。同一次提交改完所有固件路径，包括还没搬的测试和脚本：

- [firmware.py](../scripts/modular/firmware.py) 里 `firmware/boot`、`firmware/bootloader`、`firmware/diagnostics`、include 与 linker 条目（约 176–218 与 252–256 行；阶段 A 已改过的 gateware 条目不要回退）。`image.h` 移入 `bootloader/include/` 后，includes（214 行）与装配目录（252 行附近）加 `firmware/bootloader/include`。
- `own` 里的目录项必须收窄（B-R2 majors：`source_files` 对目录项做 `rglob('*')`）：`:183、:192` 的 `'firmware/diagnostics'` 改 `firmware/diagnostics/primer20k_vexriscv`（原路径搬走后根本不存在）；`:198` 的 `'firmware/bootloader'` 改 `[firmware/bootloader/primer20k_vexriscv, firmware/bootloader/include]`——不收窄就会把 `bootloader/mega138k_ae350/**` 扫进 20K bootloader 输入，违反硬规则 2 与机器隔离。
- [ae350.py:12-13](../scripts/modular/ae350.py:12) 的 `BOOTLOADER` / `PAYLOAD`。
- [ae350.py:330-337](../scripts/modular/ae350.py:330) 的 payload 构建：它用 `runtime.iterdir()` 平铺收集 `firmware/bsp/mega138k_ae350_payload/` 并只给单 `-I`。把它拆成 `runtime/dma/io` 必须同提交改文件收集与 include 路径；保持单层 `runtime/`（gpio/board_io/board_sense 一起放）也是合法选项，二选一写进提交说明。
- 相对 `#include` 同提交改：`firmware/bios/main.c:2`、`firmware/bios/console.c:4-6`（`../diagnostics/tests.h`、`../common/rgb_canvas.h`）、`firmware/diagnostics/tests.c:10,13`（`../common/rgb_canvas.h`、`../common/packet_echo.h`）、`firmware/bootloader/flash_xip.c:6`（`../common/memory_layout.h`）、examples 的 `../bios/include/bios.h`（bios_demo.c:1、nyancat_demo.c:2、tty_color_demo.c:13）与 `../common/*`（microphone_demo.c:5、microphone_stereo_demo.c:5、usb_input_demo.c:13、ethernet_demo.c:5）、monitor.c:6。`monitor.c:4` 的 `../../bootloader/image_abi.h` 在 `firmware/bootloader/` 下无对应文件（今日只有 `image.h`）——先查这个 include 的真实来源（疑似历史遗留或生成物），查明前不盲改；查明后按实际依赖归入本阶段或记为已知坏项。
- `scripts/mini_ops/probe.py:53-55`。
- [builders/payload.py:34](../scripts/builders/payload.py:34) 的默认 `--source firmware/examples/bios_demo.c`。不要改 [payload.py:12](../scripts/builders/payload.py:12)，那是不移动的 `firmware/drivers/string.c`。
- [gwbasic_sim.py:227](../scripts/gwbasic_sim.py:227) 的 `start.S` 注释。不要把 [gwbasic_sim.py:27](../scripts/gwbasic_sim.py:27) 当成移动点。
- `scripts/sd_verify.py:38` 的 `firmware/examples/sd_demo.c`；`tests/bios_libc_test.py:18` 的 `firmware/examples/libc_test.c`。
- `tests/` 下用 `../firmware/...` 的 C 文件（`bios_crc_test.c`、`console_output_test.c`、`libc_memory_test.c`、`nyancat_render_test.c`、`usb_host_enumeration_test.c`、`usb_key_map_test.c`、`vt_console_test.c`）与被移动的例子/诊断同提交改。测试文件本身可以留到阶段 D 再换目录。
- 仍在使用的命令文档同提交改：`docs/ae350-bootloader.md`、`ae350-dma.md`、`ae350-io.md`、`ae350-platform.md`、`ddr-boot.md`、`bios-payload-porting.md`、`doom.md`、`gwbasic.md`、`firmware-tests.md`，以及 README / `firmware/README.md` / `docs/system-design.md` 里指向新路径的现行句子。

**验证门**：20K bootloader（rom+xip）、hal-sdk、BIOS、doom payload 构建，加 RAM 引导冒烟。AE350 ROM bootloader、payload、diagnostics 镜像构建，加上板加载。不把 AE350 `system` 从 P1 BLOCKED 里救出来。

### 阶段 D — scripts、mini_ops、tests

阶段 P 之后剩余的 20K 脚本按 manifest 进入 `scripts/primer20k_vexriscv/`。`mini_ops` 按机器分包。`sim/` 与 `scripts/tests/` 按 §1 并入 `tests/`。子命令名不变。

同一次提交：

- 更新闭包声明到新的 `scripts/modular/machines/<id>/` 与 `scripts/primer20k_vexriscv/` 路径。
- 框架注册表分发落地：worker.py:19、30，replay.py:7-9，targets.py:5、102-103，mini.py:151，prepare_commit.py:64 不再静态 import 机器后端/机器 ops；`system.py`、`cpu.py`、`contracts.py` 必须同目录进 `machines/primer20k_vexriscv/`（system.py:90 按 `Path(__file__).parent` 哈希三者）。
- `parents` 深度位移逐文件核：`tests/*.py` 的 `ROOT=parents[1]`→`parents[2]`；`sim/*`→`parents[3]`；`scripts/` 根脚本进 `primer20k_vexriscv/`/`tools/` 后 `parents[1]`→`parents[2]`；`scripts/modular` 后端文件（hardware.py:21、firmware.py:23、cpu.py:10、rtl.py:42 等）进 `machines/primer20k_vexriscv/` 后 `parents[2]`→`parents[4]`。
- `ROOT` 硬编码脚本/资源路径（阶段 0 manifest 列全量，代表点）：gwbasic_test.py:33、doom_test.py:38、84、143、usb_fs_phy_verilog_test.py:26、usb_host_enumeration_test.py:22、cpu_copy_crc_test.py:37、scripts/test_cpu_fence.py:31、36、mini_operations_test.py:236、bios_line_editor_test.py:120、workflow_test.py:193、199、gwbasic_verify.py:77、mini_ops/linux.py:50、227、mini_ops/release.py:177、180、187（`scripts/firmware_verify.py`、`scripts/benchmark.py`、`scripts/performance_report.py`）、tests/ae350_cdc_test.py:7 的同目录兄弟 import。`scripts/builders/` 原位不动，其 `ROOT/'scripts/mini.py'` 引用（busybox.py:150、matrix.py:57）不需要改。
- 改 [firmware.py:173-174](../scripts/modular/firmware.py:173) 和 [hardware.py:25-27](../scripts/modular/hardware.py:25) 里的 `scripts/boot_image.py`、`build_records.py`、`build_recipe.py`、`versions.py`、`riscv_toolchain.py`。这些文件进 `scripts/tools/` 后，闭包继续点名它们，不把整个 `scripts/tools/` 收进某一台机器。
- [ae350_report.py:169-170](../scripts/ae350_report.py:169) 改为新测试路径。
- 现行操作文档与 skills：`AGENTS.md`、`scripts/README.md`、`docs/pre-commit-sop.md`、`docs/build-artifacts.md`、`docs/modular-build.md`、`docs/component-builds.md`、`.agents/skills/riscv-mini-*/SKILL.md`。历史表（`CHANGELOG.md`、`docs/version-history.md`、`docs/lab-environment.md` 的版本行）不改。
- `build_records.py` 的 `INPUT_DIRS` 若仍含 `sim`，改成新测试目录或删掉。
- 分发反证再跑一次：未知机器报错；改 138K `io_map` 只失效 138K。

**验证门**：`mini.py` 全部子命令 `--help`；两机 `plan`；host 测试全绿；隔离反证。

### 阶段 E — 设计文档收口

按修订四，本阶段验收跑一次两机全量重建并登记结果：AE350 gateware 必须 0/0/0.142；20K full-profile 原样登记（预期仍红 = 既有回归，除非已另行修复），连同 minimal 锚点、host 终态、隔离反证一起进阶段 E 报告。

只更新状态，不在这一步才去修已经坏掉的命令。

- `docs/multi-platform-architecture-proposal.md` 标明 §3/§4 哪些已落地、哪些仍是提案。
- `docs/component-builds.md` 写明：目录重组之后，旧发布包的 `build --locked` 会在 [replay.py:46-48](../scripts/modular/replay.py:46) 报 `Source differs from lock`。历史复现只用 `mini.py builds reproduce`（保留 checkout 自回放）。
- 本计划文尾追加实际落地与 §1 的差异。

## 4. 兼容条款

- `--machine` ID、`mini.py` 子命令与参数、镜像格式、Flash 布局不变。
- 未显式选择机器的旧命令仍只指 20K。未知机器 ID 报错，不猜测，也不落入 20K。
- `RELEASES.yaml` / `VERSIONS.yaml` 不引用源码路径，不用改。
- catalog 与 `builds reproduce` 不受影响。`build --locked <重组前的包>` 在新树上不可用，这是预期，不是要修的双路径兼容。
- 历史 `build/runs/` 回执不迁移、不改写。
- 工作区产物目录名 `build/work/<machine>/` 与源码目录解耦，不因本次改名。

## 5. 不做

- `firmware/hal/src` 与 `drivers/` 的跨机器设备接口重组。hal/src 依赖 20K 生成 CSR 头。AE350 需要 BIOS 时再按设计文档 §5 提取。
- `interfaces/` 与 requires/provides。
- AE350 HDMI、LCD 移植、把 AE350 `system` 从 P1 BLOCKED 里做出来。
- 改写 CHANGELOG 和历史版本表里的旧命令。
- 空且未跟踪的 `gowin_ddr3_100/200/300/ipgen` 目录。它们不在 git 里，不是遗漏。

完成标准：第三台机器 = 五个新目录（board、machine、bootloader、bsp、scripts 机器包）+ 注册表一项 + 一份文件级闭包。未知 id 会报错。加这台机器不需要改另外两台机器包里的文件。阶段 P 没做完之前，这条不成立，所以 P 不能省。

## 6. 评审条目的落点（第一轮评审）

| 评审项 | 本修订怎么处理 |
| --- | --- |
| 闭包声明用了阶段 D 才存在的路径；glob 空目录会静默缩水 | §2 文件允许列表 + 硬规则 3。A 不声明 D 的路径 |
| A 的重建门依赖 firmware.py:175，计划却划给 C | 阶段 A 同提交改 173–175 行 |
| `boot_upload`/`boot_image` 被 AE350 import | 阶段 P 抽公共符号，决定 5 只搬剩余的 20K 脚本 |
| 未知机器静默落入 20K | 阶段 A 改 `targets.py`，并加反证测试 |
| AE350 闭包含 `devices/audio`；DDR 不是双消费者 | §1/§2 删除这两条。不建 `gateware/memory/` |
| `replay.py:46-48` 使旧包 `--locked` 失败 | §4 写成预期；阶段 E 更新 component-builds.md |
| 框架与 20K `firmware.py` 双向 import | 阶段 P 把 helper 抽到 `compile.py` |
| 引用清单漏 docs / 错行号 | 阶段 0 manifest 为唯一清单；正文不再称某一段是全量 |
| `ae350.py:156` 被误当成回执兼容 | §0 决定 4 收窄。守卫保留 |
| A 与 B0 会把 memory 搬两次 | memory 归属只在阶段 A 发生 |
| sim 计数 | §1 按 22 个跟踪文件分配 |
| AE350 PnR 基线缺失 | 阶段 0 补记，否则 A 不能开 |

## 7. 评审条目的落点（第二轮评审，gemini-4-argon + lead 复核）

| 评审项 | 本修订怎么处理 |
| --- | --- |
| B-1 共享符号表不全：`Operation`、`uart_state.wait_for`、`programmer_cable.resolve_location`、`abi_tag` 归类 | 阶段 P 表补 4 行；`Operation`→`mini_ops/common.py`；`uart_state`/`programmer_cable` 整模块进 `scripts/tools/`；`abi_tag` 留 20K 剩余文件，import 拆两路 |
| B-1 框架→20K 后端 import 在 P 后仍在 | §1 把 hardware/firmware（剩余）/rtl/cpu/contracts/configuration/system/arguments 划为 20K 后端；worker/replay/targets/mini/prepare_commit 阶段 D 注册表分发（点名行号）；system/cpu/contracts 同目录移动（system.py:90） |
| B-2 闭包在阶段 A 点名尚不存在的 `devices/*` | gpio/ws2812 提前到阶段 A（两机 import 属实：board_io.py:3-4、ae350_board_io.py:13-14）；devices/{audio,microphone,video} 在 B 拆出提交同次进声明；audio/audio_clock/microphone/video 阶段 A 归 20K `peripherals/` |
| B-3 `native_dma.py` 无归属 | `memory/native_dma.py` 进 §1/§2；6 个 import 方阶段 A 同提交改 |
| M-1 阶段 A 清单缺 `soc.py:250/101`、`usb.py:123`、`io_map.py:7-8`、`machines.py:75`、modular/builders import 方、`sim/test_build_report.py` 今日即坏 | 阶段 A 与阶段 0 各补对应条目；test_build_report 在基线记录前独立小提交修复 |
| M-2 阶段 C 缺 `ae350.py:330-337`、相对 `#include`、`image.h` include 目录、`sd_verify.py:38`、`bios_libc_test.py:18` | 阶段 C 全部补入；`monitor.c:4` 的 `image_abi.h` 记为先查来源再动 |
| M-3 阶段 D 缺 `parents` 位移、`ROOT` 字符串清单、`ae350_cdc_test.py:7`、`mini.py:151` | 阶段 D 补入；builders 不移动，其 mini.py 引用不改 |
| 评审自报基线路径不存在（`build/store/gateware/ed502601d172/`、`ae350-current.json`、`reports/ae350-m6-acceptance.md`） | 阶段 0 改用实测路径（archives 归档两处），并显式禁止引用评审编造的路径 |
| sim host 测试只列 4 个；sim/ 无 iverilog 文件 | §1 改 7 个 host 测试；sim/ 子目录 = 13 个 migen 文件 |
| `examples/primer20k_vexriscv/` 内容未列 | §1 列出 11 个跟踪 .c |
