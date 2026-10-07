# Repository workflow

Read the operation-specific skill when performing that operation: [构建与 PnR](.agents/skills/riscv-mini-build/SKILL.md), [测试与实板验收](.agents/skills/riscv-mini-test/SKILL.md), [性能测试与对比](.agents/skills/riscv-mini-benchmark/SKILL.md), [整理与提交](.agents/skills/riscv-mini-commit/SKILL.md), [发布与最终归档](.agents/skills/riscv-mini-release/SKILL.md), [清理中间构建](.agents/skills/riscv-mini-clean/SKILL.md), [复现构建](.agents/skills/riscv-mini-reproduce/SKILL.md). Load only the skills needed for the task.

Before preparing or creating a Git commit, read [the pre-commit SOP](docs/pre-commit-sop.md). Complete its reviewed change record, local staged fingerprint check and applicable verification/performance report. Do not duplicate Git file inventories in reports. Update CHANGELOG only for a code release; documentation/workflow commits retain change records without a changelog entry. Read [version history](docs/version-history.md) when assigning a release version.

For build discovery, performance comparisons or board programming, read [build artifact organization](docs/build-artifacts.md). Select an explicit build identity and baseline; use the catalog's `current` reference for the qualified board version.

For iterative board testing, load firmware into DDR over UART (`boot_upload.py --mode uart`) and download gateware to FPGA SRAM (`--program`). Never write board Flash unless explicitly requested; Flash installation is reserved for the final accepted image.
