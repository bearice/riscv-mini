---
name: riscv-mini-benchmark
description: 在 TangPrimer-20K/riscv-mini 运行 BIOS benchmark 或对比历史性能，选择明确的候选和基线，保存三轮 all/cache 数据并生成可追溯性能报告。
---

# 性能测试与对比

在 riscv-mini 仓库根目录使用 PowerShell 和 `.venv/Scripts/python.exe`。先确认 Git 状态与操作对象；命令中的尖括号为本次实际值，参数查对应脚本 `--help`。项目外调用时解析用户级 skill junction 的 Target，其上三级为仓库根目录。

性能身份与目录读取 `docs/build-artifacts.md`，验收要求读取 `docs/pre-commit-sop.md`。

先明确候选和用户指定基线，确认各自配置、时钟与工作量；按测试顺序加载对应镜像。`--build-dir` 记录选择的镜像身份，不会自动编程，也不读回 FPGA 镜像。补测基线后恢复候选。

```powershell
$Candidate = & .venv/Scripts/python.exe scripts/builds.py path <候选完整ID>
$Run = "build/reports/performance/$(Get-Date -Format yyyyMMdd-HHmmss)-<候选标识>"
.venv/Scripts/python.exe scripts/benchmark.py --build-dir $Candidate --suite all --rounds 3 --output-dir "$Run/all"
.venv/Scripts/python.exe scripts/benchmark.py --build-dir $Candidate --suite cache --rounds 3 --output-dir "$Run/cache"
.venv/Scripts/python.exe scripts/performance_report.py --candidate <候选ID> --baseline <基线ID> `
  --candidate-results "$Run/all" --candidate-results "$Run/cache" `
  --baseline-results <基线all目录> --baseline-results <基线cache目录> `
  --output reports/performance/<本次对比名>.md
```

每次测量使用新目录。完成条件：三轮原始结果、身份与 hash、Markdown/JSON 报告可查，失败或零字节项目保留原因并排除吞吐对比，结束后的 BIOS/音频检查通过。旧数据仅有下载/UART 证据时使用 `--allow-legacy-results` 并说明关联限制。storage 默认只读；网络专用测试按任务选择。
