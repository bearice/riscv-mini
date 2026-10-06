---
name: riscv-mini-commit
description: 在 TangPrimer-20K/riscv-mini 审阅并整理 Git 提交，执行适用验证、生成改动记录、校验暂存指纹并提交；按代码发布与文档提交规则维护 changelog。
---

# 整理与提交

在 riscv-mini 仓库根目录使用 PowerShell 和 `.venv/Scripts/python.exe`。先确认 Git 状态与操作对象；命令中的尖括号为本次实际值，参数查对应脚本 `--help`。项目外调用时解析用户级 skill junction 的 Target，其上三级为仓库根目录。

提交前读取 `docs/pre-commit-sop.md`；代码发布分配版本时读取 `docs/version-history.md`。

先按 SOP 完成适用检查及人工审阅。显式暂存本次文件，再生成记录：

```powershell
.venv/Scripts/python.exe scripts/prepare_commit.py --title '<行为变化>' --change '<变化与原因>' `
  --checks build/reports/checks/workflow.json --output reports/changes/<本次记录>.md
git add reports/changes/<本次记录>.md reports/changes/<本次记录>.json
git diff --cached --check
.venv/Scripts/python.exe scripts/prepare_commit.py --title verified --change verified `
  --output reports/changes/<本次记录>.md --verify
git commit -m '<本次提交说明>'
```

硬件/固件提交还需为 prepare 指定本次 `--build` 和 `--performance-report`。暂存内容变化后重新 prepare；本地指纹丢失也重新生成。报告写行为、验收和限制，文件清单与精确范围直接查 Git。

产品代码发布更新 VERSION，prepare 增加 `--release-version <版本> --write-changelog`，提交后创建同版本 tag。文档、报告与流程提交直接按第 1、3 节确认范围并提交，不生成 `reports/changes/` 记录，版本和 CHANGELOG 不增加条目。推送按用户要求执行。

代码提交后继续生成最终产物，读取同级 `riscv-mini-release/SKILL.md`。
