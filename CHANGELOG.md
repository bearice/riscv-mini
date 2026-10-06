# Changelog

## v0.7.0 — C 扩展 full 系统与原生 fence

- full MMU/FPU 系统启用 RISC-V C 扩展，boot ROM 从 8 KiB 缩减到 4 KiB，boot 二进制从 5292 B 缩减到 3792 B。
- 采用 CPU 原生 FENCE/FENCE.I，移除外部 fence handshake 和 write-combine 设施，共享 writeback L2 为 CPU 与 DMA 维护一致性。
- DDR 队列深度改为 1，注册跨层路径并更新时序约束；full 配置 PnR setup/hold 0/0，实板指令/MMU/FPU/DDR/外设/音频/DMA 与 60 秒 soak 验收通过。
- 相对追溯版本 0.6.0（f0892da、无 C/8K ROM）提供三轮 all/cache 性能报告；cache suite 两版都有 LCD underflow，真实网络吞吐及 127-cycle ACK 的 FENCE.I 模型边界尚未关闭。

性能报告：[reports/performance/6880dee-vs-f0892da.md](reports/performance/6880dee-vs-f0892da.md)。基线必须显式选择，详见报告身份和边界。

发布二进制、Git hash、构建参数和逐文件 SHA256 见对应版本的 release.json；完整改动以 Git 记录为准，已知失败和未验收项保留在性能/验收报告中。
