# Reusable build and board operation tools

生成：2026-10-08T11:24:18+00:00；模式：staged review。

## 改动

- Add mini.ps1 and task-oriented CLI workflows for artifact discovery, configuration reuse, SRAM recovery, RAM execution, persistent updates and verification, with per-operation receipts.
- Update XIP and application regions through a DDR software SPI utility with fixed ranges, JEDEC checks, CRC framing, page readback and full independent verification before Flash boot; preserve a separately verified XIP repair operation.
- Reject legacy UART installation for XIP before hardware access, package matching CSR metadata, preserve operation logs and document failure/recovery boundaries. Full update and subsequent independent Flash verification passed on the selected v0.8.0 build; physical cold boot was not tested.

## 验证

对应源码的检查：57 项，PASS；命令和输入身份见同名 JSON。

完整改动以 Git 记录为准；本记录只保留改动说明和验证依据。
