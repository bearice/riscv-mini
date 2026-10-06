# 修正最终归档对 SD 配置的重建参数

生成：2026-10-06T03:05:37+00:00；模式：staged review。

## 改动

- 最终重建使用明确 sd-profile，避免同时传互斥的 legacy sd-backend。上一提交的最终归档启动失败日志保留，未标为 complete，未编译或下载硬件。

## 验证

对应源码的检查：10 项，PASS；命令和输入身份见同名 JSON。

性能依据：`reports/performance/6880dee-vs-f0892da.md`。

完整改动以 Git 记录为准；本记录只保留改动说明和验证依据。
