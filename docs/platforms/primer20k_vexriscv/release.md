# 20K 系统包发布

仅在明确要求正式 release 时执行。公共提交与发布规则见 [SOP](../../common/pre-commit-sop.md)。下列 BIOS、all/cache 与系统包命令适用于 `primer20k_vexriscv`；不声明 AE350 支持相同发布路径。

```sh
python scripts/mini.py release create --version <系统包版本> --hardware <配置模板> \
  --source firmware/bios/main.c --board-check --baseline <已验收基线> \
  --baseline-results <基线all目录> --baseline-results <基线cache目录>
python scripts/mini.py release verify <生成的发布包目录>
```
