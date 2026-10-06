---
name: riscv-mini-clean
description: 在 TangPrimer-20K/riscv-mini 清理已登记的中间构建目录，先检查清理计划，保留 catalog、复现 recipe、固定版本和最终发布/提交产物。
---

# 清理中间构建

在仓库根目录使用 PowerShell 和 `.venv/Scripts/python.exe`。项目外调用时解析用户级 skill junction 的 Target，其上三级为仓库根目录。先读 `docs/build-artifacts.md` 的目录与清理保护规则；以下占位符使用本次实际完整 ID。

```powershell
.venv/Scripts/python.exe scripts/builds.py list
.venv/Scripts/python.exe scripts/builds.py clean <中间构建ID>
.venv/Scripts/python.exe scripts/builds.py clean <中间构建ID> --execute
```

先查看计划，再执行。清理对象限于已登记且未被 refs 固定的 `build/runs` 中间目录。正式发布、提交快照和旧未知目录分别管理。

完成条件：目标目录清理成功，catalog/recipe 仍可查；失败保留状态与原因，修复后用同一命令重试。需要复现时读取同级 `riscv-mini-reproduce/SKILL.md`。
