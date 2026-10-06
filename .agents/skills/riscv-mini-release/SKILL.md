---
name: riscv-mini-release
description: 在 TangPrimer-20K/riscv-mini 从已提交的干净源码生成正式版本发布包或代码提交快照，归档二进制、报告、参数和 Git hash 并验证完整性。
---

# 发布与最终归档

在 riscv-mini 仓库根目录使用 PowerShell 和 `.venv/Scripts/python.exe`。先确认 Git 状态与操作对象；命令中的尖括号为本次实际值，参数查对应脚本 `--help`。项目外调用时解析用户级 skill junction 的 Target，其上三级为仓库根目录。

读取 `docs/pre-commit-sop.md` 和 `docs/build-artifacts.md`。先核对干净 HEAD、VERSION；正式发布还需同版本 tag 指向 HEAD，版本不可覆盖。尚未提交时先完成同级 `riscv-mini-commit/SKILL.md`。

涉及代码变更：先提交干净源码，再生成最终包。正式发布使用下列命令；不发布新版本的代码提交加 `--snapshot`：

```powershell
.venv/Scripts/python.exe scripts/release.py --version <VERSION> --build <配置模板ID> `
  --app firmware/bios/main.c --board-check --baseline <基线ID> `
  --baseline-results <基线all目录> --baseline-results <基线cache目录>
.venv/Scripts/python.exe scripts/release.py --verify <生成的最终包目录>
```

根据所选系统支持的功能安排板测和性能，脚本失败时保留证据并修复。完成条件：最终包 complete 且 verify 通过，含二进制、报告、参数、CPU/工具输入、版本、完整 Git hash 和文件 SHA256。正式包位于 `build/releases/v<版本>/`，提交快照位于 `build/archives/v<版本>-<Git>/`。纯文档提交无需 FPGA 重建。
