---
name: riscv-mini-test
description: 在 TangPrimer-20K/riscv-mini 选择和执行主机、RTL、DFI 回归或 FPGA 实板验收，核对固件/bitstream 身份及受影响功能，记录检查结果。
---

# 测试与实板验收

在 riscv-mini 仓库根目录使用 PowerShell 和 `.venv/Scripts/python.exe`。先确认 Git 状态与操作对象；命令中的尖括号为本次实际值，参数查对应脚本 `--help`。项目外调用时解析用户级 skill junction 的 Target，其上三级为仓库根目录。

验证矩阵读取 `docs/pre-commit-sop.md`，板测身份读取 `docs/build-artifacts.md`。

日常上板入口与支持条件读取 `scripts/README.md`。恢复用 `scripts/mini.py board recover --build <ID>`；临时运行用 `board run`；完整功能验收用 `board verify --suite firmware`；精确 Flash 读回用 `--suite flash`，不写 Flash。每次日志在 `build/operations/`；持久写入仅在用户明确要求时用 `board update`，不要拼接 Gowin/临时串口探针。

按改动选择验证层级，使用 SOP 的验证矩阵。常用主机与 L2 检查：

```powershell
.venv/Scripts/python.exe tests/config_test.py
.venv/Scripts/python.exe tests/shared_l2_test.py
.venv/Scripts/python.exe tests/workflow_test.py --report build/reports/checks/workflow.json
```

CPU/DDR/外设改动另外选择相关 `tests/*_verilog_test.py`、DFI 回归或 `scripts/test_cpu_fence.py`；先查脚本参数和所需仿真工具。模型通过后仍需实际配置 PnR 和受影响板测。

```powershell
.venv/Scripts/python.exe scripts/mini.py board verify --build <本次完整ID> --suite firmware
.venv/Scripts/python.exe scripts/builds.py pin current <本次完整ID> --evidence <本次operation目录>/firmware-verification.json
```

对支持文件系统、视频、USB、音频的完整系统增加 `--soak-seconds 60`；连接麦克风且配置支持时再加 `--mic`。最小系统选择其实际支持的测试。匹配 FS/app 两个 hash 且板测通过后才 pin；记录未跑项和听音、屏幕、物理输入等观察限制。
