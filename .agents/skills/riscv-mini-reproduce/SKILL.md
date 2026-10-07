---
name: riscv-mini-reproduce
description: 在 TangPrimer-20K/riscv-mini 根据保存的构建 ID 和 recipe 复现历史或已清理构建，在独立 checkout 恢复源码、参数与 CPU 输入并生成新产物。
---

# 复现构建

在仓库根目录使用 PowerShell 和 `.venv/Scripts/python.exe`。项目外调用时解析用户级 skill junction 的 Target，其上三级为仓库根目录。先读 `docs/build-artifacts.md` 的复现流程；以下占位符使用本次实际完整 ID 与新目录。

```powershell
.venv/Scripts/python.exe scripts/builds.py show <完整构建ID>
.venv/Scripts/python.exe scripts/builds.py reproduce <完整构建ID> --output-dir build/runs/<新复现目录>
```

确认 catalog、recipe 和匹配的工具/依赖可用。脚本在独立 detached checkout 恢复 Git 源码、VERSIONS.yaml、dirty patch、未跟踪源码、构建参数与 CPU RTL/元数据。使用新目录，保留原身份和证据。

完成条件：新产物及 validation 可查，参数和输入对应原 recipe。PnR、板测或性能按实际复验记录，复现生成不继承旧板测；需要验证时读取同级 `riscv-mini-test/SKILL.md`。
