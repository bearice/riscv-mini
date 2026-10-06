---
name: riscv-mini-build
description: 在 TangPrimer-20K/riscv-mini 创建最小或完整系统构建、编译固件、生成 RTL 或执行 Gowin 综合/PnR，核对 CPU 输入、参数与时序结果。
---

# 构建与 PnR

在 riscv-mini 仓库根目录使用 PowerShell 和 `.venv/Scripts/python.exe`。先确认 Git 状态与操作对象；命令中的尖括号为本次实际值，参数查对应脚本 `--help`。项目外调用时解析用户级 skill junction 的 Target，其上三级为仓库根目录。

构建身份与参数读取 `docs/build-artifacts.md`。

```powershell
.venv/Scripts/python.exe scripts/builds.py list
.venv/Scripts/python.exe scripts/builds.py show current
.venv/Scripts/python.exe scripts/build.py --profile minimal --purpose <本次用途>
```

最后一条生成 RTL、boot/app 固件；增加 `--synthesize` 才执行 Gowin 综合和 PnR。默认创建独立 `build/runs/`，通过本次输出获取目录和完整 ID，保存 `build-info.json`、`validation.json` 和工具日志。

full 候选根据指定模板的 `validation.json` 选择参数，显式保留 CPU RTL/生成元数据、CPU variant、功能开关、ROM/L2 大小、SD/USB 后端、音频时钟和 place/route 选项。带 C 的 CPU 使用已核验的 `--cpu-verilog` 或 `--cpu-rtl-dir`，不能靠 `--profile full` 推断 C 支持。调整 CPU 时再查 `scripts/cpu_generate.py --help`。

PnR 完成条件：实际目标配置的 setup/hold 均为 0；生成成功只证明编译完成。`latest-generated` / `latest-pnr` / `current` 分别代表最新生成、最新 PnR 通过和已固定的板测版本。
