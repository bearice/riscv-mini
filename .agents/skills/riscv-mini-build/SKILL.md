---
name: riscv-mini-build
description: 在 TangPrimer-20K/riscv-mini 创建最小或完整系统构建、编译固件、生成 RTL 或执行 Gowin 综合/PnR，核对 CPU 输入、参数与时序结果。
---

# 构建与 PnR

在 riscv-mini 仓库根目录先运行 `nix develop`，使用 flake 管理的 Python、依赖和 xPack RISC-V 工具链；Linux/NixOS 使用 `python` 或 `.venv/bin/python`。Windows PowerShell 使用 `.venv/Scripts/python.exe`。先确认 Git 状态与操作对象；命令中的尖括号为本次实际值，参数查对应脚本 `--help`。项目外调用时解析用户级 skill junction 的 Target，其上三级为仓库根目录。

构建身份与参数读取 `docs/build-artifacts.md`。

所有构建统一使用 `scripts/mini.py build <target>`，计划使用 `scripts/mini.py plan <target>`。先看 `scripts/mini.py build --help` 和 `docs/component-builds.md`，不得恢复已删除的独立构建脚本或自行分解 prepare/execute/record 阶段。

```sh
python scripts/mini.py builds show current
python scripts/mini.py plan bios --hardware current
python scripts/mini.py build bios --hardware current
python scripts/mini.py build system --profile minimal --purpose smoke
python scripts/mini.py build system --hardware current --refresh-hardware --synthesize
```

固件目标复用选定 CPU/SoC/bootloader/位流；硬件参数修改统一使用 system、soc-description 或 gateware 目标。`--feature NAME=on|off` 控制功能，`--source` 选择应用，`--hardware` 选择硬件输入。只有 `--synthesize` 才运行 Gowin；构建不编程板子。

CPU 生成使用 `mini.py build cpu --generate`；完整 CPU 输入和生成参数读取该目标的 `--help`。Java/sbt 自动发现，工具身份和全部 flags 写入 lock。消费新 CPU 用明确的 `--cpu-verilog` 及配对 metadata，不能靠文件名或 profile 推断 C、MMU、FPU 或流水线参数。profile minimal 可从零构建；full 需要明确的 CPU 输入。

Linux、BusyBox、OpenSBI、U-Boot、payload、DOOM、GW-BASIC、CPU qualification 和 matrix 同样是 build/plan 目标，参数查对应目标 help。Linux 工具由 native/WSL runner 自动绑定，HOSTCC 等角色写入 run；不要手动执行生成的 build.sh 或重复运行打包阶段。

从本次输出取得 `build/runs/<id>/result.json`，确认 passed、system/artifact、resolved flags 与复用步骤。`--output-dir` 仅在需要显式输出视图时使用。PnR 完成条件是实际目标 setup/hold 均为 0，RTL/编译通过不代表板测通过。新候选保持 current 不变。
