# Fix DOOM wall projection for unsigned angle differences

生成：2026-10-12T08:39:11+00:00；模式：staged review。

## 改动

- Restore abs(int) conversion and single evaluation in the payload shim; RV32 O0/O2 regression fails before the fix and passes afterward. DOOM suite: 11 tests OK. Same-camera E1M5 QEMU comparison and four board frames confirm removal of false vertical walls; board blit diff is zero. Evidence: build/doom-debug/fixed-20261012 and build/doom-render-sim-fixed.

## 验证

对应源码的检查：18 项，PASS；命令和输入身份见同名 JSON。

完整改动以 Git 记录为准；本记录只保留改动说明和验证依据。
