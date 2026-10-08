# 发布 RAM 零基址与 Flash XIP 系统 0.8.0

生成：2026-10-08T03:02:47+00:00；模式：staged review。

## 改动

- 为已合入且验收的 XIP/内存布局功能分配组件版本 rtl/bootloader/hal/bios/opensbi 0.8.0、uboot 0.2.0，并在 RELEASES.yaml 固定系统组合；OpenSBI/U-Boot 继续独立构建验收。
- 正式包由本提交后的干净源码重建，先前提交快照仅为功能阶段验证依据；不继承 dirty 镜像作为发布二进制。

## 验证

对应源码的检查：18 项，PASS；命令和输入身份见同名 JSON。

性能依据：`build/archives/v0.7.2-0c54070bc194/full-rv32imaf-rom0k-l24k/reports/performance/comparison.md`。

完整改动以 Git 记录为准；本记录只保留改动说明和验证依据。
