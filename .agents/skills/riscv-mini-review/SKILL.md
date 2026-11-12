---
name: riscv-mini-review
description: 在 riscv-mini 检查 GitHub PR 审查意见（PR reviews / inline comments），分类未解决项，执行针对性修复、运行回归验证、提交推送并使用工具脚本自动回复与标记。
---

# GitHub PR 审查处理（PR Review Workflow）

本 Skill 提供 riscv-mini 项目处理 GitHub PR 审查意见（包含自动化机器人 Codex、协作审查者等）的标准工作流与自动化脚本。

## 1. 常用操作脚本

使用专用的 Python 辅助工具：
```sh
nix develop --command python3 .agents/skills/riscv-mini-review/scripts/gh_review.py <command>
```

### 命令列表

1. **检查与分类审查意见**：
   ```sh
   nix develop --command python3 .agents/skills/riscv-mini-review/scripts/gh_review.py list [--pr <number>]
   ```
   - 自动获取当前分支对应的 PR（或指定 `--pr`）。
   - 将审查意见分类为：
     - **Active Pending**：当前最新代码上尚未回复/解决的活跃审查意见；
     - **Active Replied**：当前最新代码上已有回复与标记的审查意见；
     - **Outdated**：旧提交上已被后续提交覆盖失效的历史意见（加 `--show-outdated` 查看）。

2. **回复并标记解决（Resolve）**：
   ```sh
   nix develop --command python3 .agents/skills/riscv-mini-review/scripts/gh_review.py resolve <comment_id> --body "技术解决说明与对应 commit hash" [--reaction +1]
   ```
   - 一键完成向评论发送回复（reply）并添加指定 reaction（默认 `+1`）。

3. **单项回复（Reply）**：
   ```sh
   nix develop --command python3 .agents/skills/riscv-mini-review/scripts/gh_review.py reply <comment_id> --body "说明文本"
   ```

4. **单项反应（React）**：
   ```sh
   nix develop --command python3 .agents/skills/riscv-mini-review/scripts/gh_review.py react <comment_id> [--reaction +1]
   ```

---

## 2. 标准处理流程（SOP）

1. **发现与分类**：
   运行 `gh_review.py list`，提取所有 Active Pending 审查意见的 ID、文件路径、行号与内容。
2. **分析与修复**：
   - 阅读意见指出具体问题（如位宽溢出、对齐、时序、协议竞争等）；
   - 在对应源码中进行针对性修改；
   - 补充或更新主机回归测试用例。
3. **本地验证**：
   - 运行受影响模块的主机/RTL 仿真与单元测试（如 `gfx2d_host_test.py`、`test_ae350_platform.py`）；
   - 若改动影响 Gateware 且用户要求，执行 PnR 时序闭合与实机验证。
4. **提交与推送**：
   - 按照提交规范创建 Git commit（信息明确指出修复了哪条 review 问题）；
   - 推送至当前 PR 分支。
5. **标记解决**：
   - 运行 `gh_review.py resolve <comment_id> --body "Addressed in commit <hash>: ..."` 逐条闭环；
   - 再次运行 `gh_review.py list` 确认所有 Active 意见均已处于 Replied 状态。
