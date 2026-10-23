---
name: riscv-mini-commit
description: 审阅并完成 riscv-mini 的普通 Git 提交，明确选择暂存内容并检查提交结果。
---

# 普通 Git 提交

在仓库根目录操作，按 [提交 SOP 的普通提交部分](../../../docs/common/pre-commit-sop.md#普通提交) 执行。

1. 查看 Git 状态、diff 与新增文件，确认本次要提交的内容。
2. 明确暂存选定文件，审阅 `git diff --cached`，运行 `git diff --cached --check`。
3. 写清改动与原因，创建提交，检查提交后的状态。推送按用户要求执行。

提交检查限于基本 Git 检查；测试与构建只按当前任务需要或用户要求执行，已有结果如实说明。提交不修改版本、CHANGELOG 或 tag，也不追加构建、归档或快照。

完成条件：本次提交已创建，报告提交 hash、摘要与剩余工作树状态。
