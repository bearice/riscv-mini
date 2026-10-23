# U-Boot Ethernet ring

适用机器：`primer20k_vexriscv`。先在机器 JSON 设置 `rtl.features.eth_dma`，再构建匹配硬件；驱动消费选定硬件的导出。

U-Boot 使用与 HAL 相同的 16-byte DDR descriptors，RX/TX 各四个、1536-byte buffer。硬件通过 buffered native DDR 端口收发，MAC packet SRAM 是私有 staging，不映射到 CPU。RX 借用 DDR buffer，`free_pkt` 归还所有权；TX 复制到受驱动管理的 DDR buffer，发布 descriptor 并等待硬件完成。停止及移除设备前排空 DMA，超时保留缓冲，避免硬件访问已释放内存。

驱动通过编译选项选择唯一后端。DMA 版二进制不包含 PIO compatible 或搬包循环；关闭 DMA 的独立 PIO 配置保留原功能。不存在 DMA 运行失败后转 PIO 的路径。`mini.py build uboot --hardware <构建目录>` 从 CSR 选择后端及早期 UART 地址；OpenSBI 从相同构建生成 Ethernet/USB 中断、ring register offsets 和实际 CPU ISA。


新硬件/软件组合仍须独立验证 ARP、ping、TFTP 长度与 CRC、停止/恢复及并发；TX 完成不代表对端接收。
