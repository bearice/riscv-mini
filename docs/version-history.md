# 发布版本追溯

## 组件版本与系统包

版本是给人看的标签；组件之间的真实兼容性由内容哈希保证——每个镜像内嵌 CSR `abi_tag`，RTL 身份是 `validation.json` 的 `rtl_sha256`。所以"旧 RTL 跑新 BIOS"在机制上本来就成立，不需要版本号相等。版本号只用于沟通"这是哪一版"。

因此版本按组件独立递增，记录在 `VERSIONS.yaml`（六个组件：`rtl`、`bootloader`、`hal`、`bios`、`opensbi`、`uboot`）。只有真正改动的组件才递增自己的版本，未改的组件保持原版本。`hal` 是静态链接进 BIOS 的源码库，它的版本是溯源标签（BIOS 构建记录"built against hal vX"），兼容性仍走 `abi_tag`。新组件从 `0.1.0` 起；既有组件继承最后一次合并发布的版本（0.7.1）。

真正上板验收、打包发布的是**系统包**：一组被验证过能一起工作的组件版本，记录在 `RELEASES.yaml`。`scripts/release.py --version <包版本>` 按 `RELEASES.yaml` 里钉住的组件组合构建整 SoC 包，打 `system/v<包版本>` tag。组件单独发布打 `<组件>/v<版本>` tag（如 `bios/v0.7.2`、`uboot/v0.1.0`）。只有 `rtl` 版本变化才重跑 PnR 与性能（见 [提交前 SOP](pre-commit-sop.md)）。

读取入口是 `scripts/versions.py`：`read_versions()`、`component_version()`、`read_releases()`、`bundle_versions()`。`build.py` 把各组件版本写进 `features.h`（`MINI_VERSION_*`）和 `validation.json` 的 `component_versions`，构建身份 `build_id` 用被构建应用组件的版本。

## 合并版本时代（0.1.0–0.7.1）

0.7.1 及之前所有组件共享一个版本号。以下是 2026-10-06 从完整 Git log 建立的功能阶段映射，并已为每个阶段补建 annotated tag。v0.7.1 与 v0.7.0 都是带实板验收的正式代码发布，v0.7.1 是合并版本时代的最后一版；每个包额外保存完整 Git hash。

| 追溯版本 | 功能阶段末提交 | 代码能力依据 |
| --- | --- | --- |
| 0.1.0 | 88ece43 | RV32IM 最小系统、DDR/LCD 60/120 MHz 并发、Flash/UART boot |
| 0.2.0 | 60b42a4 | HAL、Dock IO、SD/FatFs、音频 DMA、Ethernet、USB HID、双麦克风、模块化配置 |
| 0.3.0 | f47e5f2 | full MMU/FPU、紧凑 SD/USB/DDS 音频、硬件 DDR boot、初始共享 L2 |
| 0.4.0 | ca78a2d | 常驻 BIOS、SD/TFTP boot、USB hub、演示、OpenSBI、benchmark、libc/SD 优化 |
| 0.5.0 | ff84bfb | 原生 crossbar、快速 L2 refill/burst、CPU fence handshake，write-through 阶段 |
| 0.6.0 | f0892da | CPU/DMA 共享一致性 writeback，L2 RAM boot stack；无 C、8 KiB ROM，当前性能基线 |
| 0.7.0 | 6880dee | C 扩展、4 KiB ROM、原生 fence、移除外部 fence/write-combine、DDR 深度 1 与注册路径 |
| 0.7.1 | 功能提交（代码+VERSION bump+changelog 同提交）；v0.7.1 tag 指向其后的变更记录提交 | ROM/BIOS/`status` 打印构建身份（semver+commit+config 指纹、RTL 与 ROM 版本），`validation.json` 记录 `build_id`/`rtl_sha256`/`config_sha256` |

## 各版本的 tag 与构建包

2026-10-06 为 0.1.0–0.6.0 补建了 annotated tag，每个 tag 指向该功能阶段的末提交；v0.7.0 从 squash 之后的 fe4a30c 改指到该阶段实际的末提交 6880dee。tag 只声明"这个版本对应这个提交"，不表示历史上做过发布验收。

每个历史版本另有一份**构建包**，位于 `build/releases/v<版本>/<配置>/`，生成方式：

- 在 `build/worktrees/v<版本>/` 的 detached worktree 检出该 tag，用**该提交自己的** `scripts/build.py --synthesize` 构建，仓库脚本一行不改。0.1.0/0.2.0 带 8 KiB 集成 SRAM、0.5.0 的 `--l2-mode/--memory-scheduler` 与 0.6.0 之前的 ROM 硬编码都不在 HEAD 的 build.py 里，只有各自提交的脚本能表达这些配置。
- CPU RTL 由该提交的 `scripts/cpu_generate.py` 重新生成（VexRiscv b6118e5c、Java 8、sbt-launch 1.9.7、2 KiB I/D cache），随包保存在 `inputs/cpu/` 与 `replay/inputs/cpu/`；0.1.0/0.2.0 用 pythondata 内置的 `lite` 变体，没有 CPU 输入。
- 工具与依赖用当前机器：Gowin V1.9.12.04、xPack riscv-none-elf-gcc 15.2.0、该提交 `requirements.lock` 对应的 .venv。历史 PnR 结果不要求与当年一致。
- 包内 `release.json` 标记 `release_kind: "code-release"`，逐文件 SHA256 可用 `scripts/release.py --verify` 校验；`reports/validation.original.json` 是该提交 build.py 的原始报告，顶层 `validation.json` 在其基础上补写 `final_git_commit`、`release_version`，以及 0.1.0/0.2.0 缺失的 `l2_size_bytes` 和用于命名的 `profile` 规范化，补写字段列在 `annotated_fields`。实板测试与基准完成后，`release.json` 写入 `board_checked: true`、`board_test`、`board_checks`、`board_evidence`、`board_tested_at` 与 `performance`，原始报告与 UART 日志保存在 `reports/`，基准数据保存在 `reports/performance/<suite>/`。
- 每个包同时登记为 `build/catalog` 中的一条记录，出现在 `scripts/builds.py list`；实板通过后状态为 `board-qualified`。它们不会成为 `current`/`baseline` 引用。

| 版本 | tag 提交 | 包配置 | ISA | ROM/L2 KiB | Logic | BSRAM | bitstream 前缀 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0.1.0 | 88ece43 | base-rv32im-rom8k-l20k | rv32im | 8/0 | 7347/20736 | 16/46 | `81ec497f…` |
| 0.2.0 | 60b42a4 | full-rv32im-rom8k-l20k | rv32im | 8/0 | 16275/20736 | 36/46 | `65f206ed…` |
| 0.3.0 | f47e5f2 | full-rv32imaf-rom4k-l24k | rv32imaf | 4/4 | 18820/20736 | 43/46 | `768839f7…` |
| 0.4.0 | ca78a2d | full-rv32imaf-rom4k-l24k | rv32imaf | 4/4 | 19117/20736 | 43/46 | `7abfe464…` |
| 0.5.0 | ff84bfb | full-rv32imaf-rom4k-l24k | rv32imaf | 4/4 | 19415/20736 | 43/46 | `0444301e…` |
| 0.6.0 | f0892da | full-rv32imaf-rom8k-l24k | rv32imaf | 8/4 | 19636/20736 | 44/46 | `d5f6f8d7…` |

六个包的综合与时序均为 0 setup / 0 hold 违例。2026-10-06 按版本顺序逐个把它们烧到板上，用**该提交自己的**验收脚本做实板测试，并运行该提交自带的基准；现场条件见 [本机测试环境](lab-environment.md)。

| 版本 | 上板验收脚本 | 通过检查数 | 基准 |
| --- | --- | --- | --- |
| 0.1.0 | `scripts/boot_verify.py --program --install`（该提交没有 `firmware_verify.py`） | 20 | 该提交没有基准工具 |
| 0.2.0 | `scripts/firmware_verify.py --program --soak-seconds 60 --mic` | 34 | 同上 |
| 0.3.0 | 同上 | 38 | 同上 |
| 0.4.0 | 同上 | 39 | `--suite all` 三轮全部 PASS（该提交没有 `cache` 组） |
| 0.5.0 | 同上 | 41 | `--suite cpu`、`--suite cache`、`--suite io` 三轮全部 PASS；`--suite mem` 失败，见下 |
| 0.6.0 | 同上 | 43 | `--suite all` 与 `--suite cache` 三轮全部 PASS |

`--suite net` 没有运行：它要求主机侧网卡持有板子配置的 server 地址 `169.254.25.153`，当时那块 USB 网卡没有分配地址。

0.5.0 的 `bench mem` 在 1 MiB 尺寸上自检失败，两次运行都可复现：`mem.read32 size=1048576 ... check=007e0000 FAIL` 与 `mem.copy32 size=1048576 ... check=0003ffff FAIL`，而同一次运行里 `mem.write32` 与 64 KiB 及以下尺寸全部 PASS，`test l2`、`test ddr`、`test soak` 也都通过。同一份 `firmware/bios/benchmark.c` 的 `memory()` 在 0.4.0 与 0.6.0 上通过，两处的 `mem.read32 size=1048576` 都是 `check=fffe0000 PASS`；差异只在 0.5.0 的 L2 配置（`CONFIG_L2_MODE 4` 的 burst refill 加 `--memory-scheduler crossbar`、`--l2-fast`）。所以该包只保存 cpu/cache/io 三组数据，mem 组没有数据；这条差异记在这里，不作为已定位的根因。

0.5.0 第一次上板时 boot ROM 报 `Hardware DDR initialization failed; reset required`，同一构建重试即通过，属于现场状态。0.6.0 的性能基线仍指向原来的 `f0892da-full-rv32imaf-rom8k-l24k-baseline-noc-rom8k-86b26cda` 测量，这些包不替换基线。

c6c52d0、0b35dbd、9f1554f 是构建/提交管理设施，不构成新的产品代码发布；文档和测试报告整理也不递增版本。正式发布把该功能阶段的最终源码、工具/配置和验证结果绑定到一个 vMAJOR.MINOR.PATCH tag；历史阶段可以补建 tag 与构建包，但必须写明未做实板验收，不能当作历史上的发布验收。

新功能或显著架构变化递增 MINOR；同一功能阶段的产品代码修复递增 PATCH；达到约定的稳定兼容接口后再递增 MAJOR。文档及流程变更不创建代码发布，仅在需要构建的代码提交后保留版本加 Git hash 的快照。版本已经发布后不能覆盖其 tag 或产物。
