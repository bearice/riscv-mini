# sim: repair stale pnr_report import in test_build_report

生成：2026-10-21T09:18:34+00:00；模式：staged review。

## 改动

- test_build_report imported pnr_report from the removed scripts.build module and failed at HEAD 5acca4d; point it at scripts.modular.rtl where pnr_report now lives, restoring a green host baseline before the reorg stage-0 record.

## 验证

对应源码的检查：20 项，PASS；命令和输入身份见同名 JSON。

完整改动以 Git 记录为准；本记录只保留改动说明和验证依据。
