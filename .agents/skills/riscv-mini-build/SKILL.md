---
name: riscv-mini-build
description: 在 TangPrimer-20K/riscv-mini 创建最小或完整系统构建、编译固件、生成 RTL 或执行 Gowin 综合/PnR，核对 CPU 输入、参数与时序结果。
---

# 构建与 PnR

## 铁律：重复构建必须使用相同 workspace

同一 machine/target 的重复构建必须复用同一个 workspace，包括修改源码、调整参数、时序收敛、失败重试和复验。已有 `--workspace` 就沿用原值，未指定时始终使用 `default`。严禁按尝试次数、阶段、时间戳、用途或参数组合更换 workspace；严禁为并行重试另开 workspace。重复构建在原工作区串行执行，保留编译器、Ninja 和 Gowin 缓存。使用 `--purpose` 和自动生成的 run 收据区分尝试，不能用 workspace 名充当运行编号。

在 riscv-mini 仓库根目录先运行 `nix develop`，使用 flake 管理的 Python、依赖和 xPack RISC-V 工具链；Linux/NixOS 使用 `python` 或 `.venv/bin/python`。Windows PowerShell 使用 `.venv/Scripts/python.exe`。先确认 Git 状态与操作对象；命令中的尖括号为本次实际值，参数查对应脚本 `--help`。项目外调用时解析用户级 skill junction 的 Target，其上三级为仓库根目录。

构建身份与参数读取 `docs/build-artifacts.md`。

所有构建统一使用 `scripts/mini.py build <target>`，计划使用 `scripts/mini.py plan <target>`。先看 `scripts/mini.py build --help` 和 `docs/component-builds.md`，不得恢复已删除的独立构建脚本或自行分解 prepare/execute/record 阶段。

```sh
python scripts/mini.py builds show current
python scripts/mini.py plan bios --hardware current
python scripts/mini.py build bios --hardware current
python scripts/mini.py build system --machine primer20k_vexriscv --purpose smoke
python scripts/mini.py build system --hardware current --refresh-hardware --synthesize
```

固件目标复用选定 CPU/SoC/bootloader/位流；硬件参数修改统一使用 system、soc-description 或 gateware 目标。功能、CPU/IP 特性及硬件参数只编辑机器 JSON，`--source` 选择应用，`--hardware` 选择硬件输入。只有 `--synthesize` 才运行 Gowin；构建不编程板子。

CPU 生成使用 `mini.py build cpu`，硬件目标也会自动解析并生成 IP。参数来自机器 JSON；Java/sbt/厂商工具自动发现，生成身份包含参数、源码、SDK 和工具。CLI 不提供 profile、feature、CPU RTL 或缓存大小覆盖。

Linux、BusyBox、OpenSBI、U-Boot、payload、DOOM、GW-BASIC、CPU qualification 和 matrix 同样是 build/plan 目标，参数查对应目标 help。Linux 工具由 native/WSL runner 自动绑定，HOSTCC 等角色写入 run；不要手动执行生成的 build.sh 或重复运行打包阶段。

从本次输出取得 `build/runs/<id>/result.json`，确认 passed、system/artifact、resolved flags 与复用步骤。`--output-dir` 仅在需要显式输出视图时使用。PnR 完成条件是实际目标 setup/hold 均为 0，RTL/编译通过不代表板测通过。新候选保持 current 不变。
