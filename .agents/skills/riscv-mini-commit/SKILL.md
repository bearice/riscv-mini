---
name: riscv-mini-commit
description: 在 riscv-mini 审阅并完成普通 Git 提交；普通提交只做基本 Git 检查，完整验收与归档仅用于明确的正式 release。
---

# 整理与提交

先确认仓库、Git 状态与本次改动，读取 `docs/pre-commit-sop.md`。Linux/NixOS 使用项目 Nix shell 中的 Python；Windows 使用 `.venv/Scripts/python.exe`。

## 普通提交

1. 审阅 diff、新增文件和本次行为，明确选择暂存文件。
2. 审阅 `git diff --cached`，运行 `git diff --cached --check`。
3. 写清提交说明，执行 Git commit 并检查状态；推送按用户要求。

普通提交不要求测试、构建、PnR、实板、性能报告、改动记录、`prepare_commit.py` 或本地暂存指纹，不改版本、CHANGELOG 或发布 tag，也不在提交后自动生成快照。已有结果如实说明即可；只完成用户另行要求或解决当前问题所需的针对性检查，不为提交补齐 release 验收。

完成条件是本次提交完成；不要自动继续执行 release skill。

## 只有明确发布时

用户明确要求正式 release 才读取同级 `riscv-mini-release/SKILL.md`，按整个发布范围执行适用验收、版本管理和最终归档。可选的 `prepare_commit.py` 默认轻量模式；`--release` 或 `--release-version` 才启用发布检查，普通提交无需调用。
