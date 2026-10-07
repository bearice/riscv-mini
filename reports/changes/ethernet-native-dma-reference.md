# 关闭音频后的 native Ethernet DMA 参考实验

本记录按用户要求保存独立实验分支，暂不合并，不发布版本。

DDR 侧采用与 SD 一致的 32-bit MemoryPort 和每端口 16 B 缓冲，MAC packet SRAM 侧保留 Wishbone；CPU 逐包启动，HAL 同步等待，没有 descriptor ring。实验关闭音频输入和输出，默认构建仍关闭 eth_dma。

提交前主机/RTL 回归通过，源码输入指纹匹配捕获的 native 候选；候选 PnR setup/hold 0/0，但实板 SD 初始化失败，未达到 board-qualified，未运行其性能基准或外部网络验收。SD RX base CSR bit 27 的异常读回与另一布局未布通结果完整保留在 [实验报告](../../docs/ethernet-dma-experiment.md)。

项目标准 prepare 流程要求 RTL 候选通过实板与性能验收；本次按用户明确要求保存失败实验，使用同一暂存指纹和 --verify 检查，单独记录该例外，不伪造成功状态。先前 Wishbone 对照的性能报告仅用于参考，不是 native 候选的验收。

同名 JSON 保存主机检查、构建身份和实际失败结果；完整改动以 Git 记录为准。已有候选和 recipe 原样保留，不改写为提交后发布产物。
