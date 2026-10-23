---
name: riscv-mini-build
description: 在 riscv-mini 按指定机器构建固件、CPU/IP、RTL 或上游目标，执行所需的 Gowin 综合与 PnR。
---

# 构建与 PnR

读取 [组件构建](../../../docs/common/component-builds.md)、[机器配置](../../../docs/common/platform-descriptions.md)，按 [平台索引](../../../docs/README.md) 确认目标机器支持的目标与产物。
Linux/NixOS 使用项目 Nix shell；其他环境与工具发现见 [开发环境](../../../docs/common/development.md)。

## 铁律：重复构建必须使用相同 workspace

同一 machine/target 的重复构建必须复用同一个 workspace，包括修改源码、调整参数、时序收敛、失败重试和复验。已有 `--workspace` 就沿用原值，未指定时始终使用 `default`。严禁按尝试次数、阶段、时间戳、用途或参数组合更换 workspace；严禁为并行重试另开 workspace。重复构建在原工作区串行执行，保留编译器、Ninja 和 Gowin 缓存。使用 `--purpose` 和自动生成的 run 收据区分尝试，不能用 workspace 名充当运行编号。

## 操作

所有目标使用 `scripts/mini.py plan <target>` / `build <target>`。先查目标 `--help`，再确认机器与输入身份：

```sh
python scripts/mini.py plan gateware --machine <machine>
python scripts/mini.py build gateware --machine <machine> --synthesize
```

硬件参数只编辑 Board/Machine JSON；CPU/IP 自动生成，软件使用选定硬件的导出。`--hardware` 选择准确硬件身份，使用 current 前核对所属机器。20K BIOS-only 构建等平台用法见对应平台文档。

`--synthesize` 才运行 Gowin。上游目标由统一入口调用 native/WSL runner，不手工执行生成的阶段脚本。日常输出使用受管理的 work/store/runs；额外交付视图才显式设置 `--output-dir`。

完成后读取本次 `build/runs/<id>/result.json`，报告实际产物、配置、复用或失败原因。PnR 同时核对 setup、hold 和工具要求的最小脉宽；编译/生成通过与实板通过分别记录。构建不编程板子，不自动改变 current。
