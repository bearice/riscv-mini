# 发布版本追溯

仓库此前没有发布 tag 或版本文件。以下是 2026-10-06 从完整 Git log 建立的功能阶段映射，不表示历史上已经发布或验证了这些版本。首次正式版本为 v0.7.0，VERSION 保存 0.7.0，正式 tag 指向本次最终提交；每个包额外保存完整 Git hash。

| 追溯版本 | 功能阶段末提交 | 代码能力依据 |
| --- | --- | --- |
| 0.1.0 | 88ece43 | RV32IM 最小系统、DDR/LCD 60/120 MHz 并发、Flash/UART boot |
| 0.2.0 | 60b42a4 | HAL、Dock IO、SD/FatFs、音频 DMA、Ethernet、USB HID、双麦克风、模块化配置 |
| 0.3.0 | f47e5f2 | full MMU/FPU、紧凑 SD/USB/DDS 音频、硬件 DDR boot、初始共享 L2 |
| 0.4.0 | ca78a2d | 常驻 BIOS、SD/TFTP boot、USB hub、演示、OpenSBI、benchmark、libc/SD 优化 |
| 0.5.0 | ff84bfb | 原生 crossbar、快速 L2 refill/burst、CPU fence handshake，write-through 阶段 |
| 0.6.0 | f0892da | CPU/DMA 共享一致性 writeback，L2 RAM boot stack；无 C、8 KiB ROM，当前性能基线 |
| 0.7.0 | 6880dee | C 扩展、4 KiB ROM、原生 fence、移除外部 fence/write-combine、DDR 深度 1 与注册路径 |

c6c52d0、0b35dbd、9f1554f 是构建/提交管理设施，不构成新的产品代码发布；文档和测试报告整理也不递增版本。正式发布把该功能阶段的最终源码、工具/配置和验证结果绑定到一个 vMAJOR.MINOR.PATCH tag；历史阶段仅保留 Git 映射，不创建虚假的历史发布包。

新功能或显著架构变化递增 MINOR；同一功能阶段的产品代码修复递增 PATCH；达到约定的稳定兼容接口后再递增 MAJOR。文档及流程变更不创建代码发布，仅在需要构建的代码提交后保留版本加 Git hash 的快照。版本已经发布后不能覆盖其 tag 或产物。
