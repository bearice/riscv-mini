# 可选 native DMA 后端

默认 `--dma-backend wishbone` 保持现有已验收路径。`--dma-backend native`
是实验后端；仿真和固件编译通过不表示布局布线、实板数据传输或性能已经通过。
两种后端均保持 CPU/总线 60 MHz、DDR CK 120 MHz 和原有外设功能。
完整候选可用 `--dma-backend native --dma-shared --place-option 2` 构建，
已能完成布局布线且没有 setup/hold 违例，但仍未通过完整实板验收：
SD 命令回显/CRC 检查失败，外部 UDP 回显也未完成。因此暂时不替换默认后端。

## 数据路径

- SD `lite` 的读写 DMA 在 32 位 SD stream 与 128 位 DDR beat 之间打包/拆包。
  保留原有 SD FIFO，最多一次 4 KiB；`full` 仍使用原有 Wishbone DMA。
- 音频保留 PCM ring 的 producer/fetched 所有权和现有 FIFO。每个已提交
  PCM word 读取一个 DDR beat，再选择其中一个 word；不缓存同一行的后续样本，
  避免读取尚未提交的 producer 数据。
- Ethernet 保留 MAC 的两 RX / 两 TX packet SRAM 槽，增加 SRAM 与 DDR 之间的
  紧凑 copy DMA：一次处理一个 32 位 word，DDR beat 使用 lane 选择和字节掩码，
  以减少宽寄存器和选择器。局部 Wishbone 仲裁只连接 MAC SRAM，不扩展全局 Wishbone master
  仲裁器。它没有改变 MAC 接收缓冲容量，也不代表实现了线速网络。

SD、音频和网络 DMA 共用一个事务仲裁器，每次命令保持所有权至写数据接受或
读响应消费完毕。默认实验方案向 LiteDRAM 请求额外 native 端口；
`--dma-shared` 改用现有 CPU/显示端口的三方调度，显示优先，CPU/DMA 轮转。
该选项仅对 native 后端有意义，不增加时钟或 PLL。

## 地址、缓存与调用约束

SD lite 的 DMA 地址要求 16 字节对齐，长度为 4 字节倍数、1..4096 字节。
HAL 继续通过对齐 bounce buffer 支持任意应用缓冲地址。紧凑硬件边界检查
保留 DDR 最后 4 KiB，DMA 起始地址必须小于 `0x47fff000`。

网络 HAL 对至少 64 字节、16 字节对齐的 DDR 缓冲使用 DMA；其他请求保持 PIO。
网络 DMA 的硬件长度范围为 14..1518，HAL 仍限制 Ethernet 帧为 14..1514。
RX 的最后一个 beat 使用逐字节写掩码；TX 可读取末尾补齐到 16 字节的内容，
只有帧的实际长度交给 MAC 发送。调用方应让缓冲尾部的补齐区域保持可读取。
网络 DMA 为同步调用，当前没有独立完成 IRQ。

native DMA 绕过共享 L2。CPU 写直达 DDR，发布数据前执行 fence；SD/网络读入
DDR 完成后，HAL 请求 L2 tag 失效并等待完成，然后失效 CPU D-cache。
该路径不支持实验性的 writeback L2。缓冲在 DMA 完成前必须保持有效和独占。

## 检查入口

`test dma` 检查 SD block、静音音频及网络 DMA 非法配置拒绝。
`test eth` 输出完成的 DMA RX/TX copy 计数；真实网络载荷仍需外部主机回显验证。
`sim/test_native_dma.py` 覆盖 SD 部分 beat、停止、网络尾部写掩码、延迟响应和仲裁；
`sim/test_memory.py --dma-shared` 检查 CPU/显示/DMA 并发和读响应背压。
`tests/l2_test.py --maintenance --refill-bypass` 检查 DMA 写入后缓存失效。
