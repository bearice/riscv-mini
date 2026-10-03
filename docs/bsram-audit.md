# 当前 BSRAM 分配

默认 full：4 KiB ROM、无集成 SRAM、4 KiB L2。实际原语分配与 PnR 总数为 43 / 46，余三块。

| 模块 | BSRAM | 用途 |
| --- | ---: | --- |
| Ethernet | 14 | 两个 RX 槽、按字节写的两个 TX 槽、收发 CDC FIFO |
| CPU | 12 | 整数寄存器文件 2、I-cache 2、D-cache 5、浮点寄存器文件 3 |
| Boot ROM | 2 | 4 KiB 启动代码 |
| DDR 控制器 | 1 | SDRAM 控制器内部存储 |
| L2 | 5 | 四个 SP 数据存储、一个 SP tag 存储 |
| RGB LCD | 4 | 512 × 130-bit 读取 FIFO，8 KiB 像素数据及标志 |
| 原生 SD lite | 2 | DMA 读/写字节 FIFO |
| 双麦克风 | 2 | 512 × 48-bit 快照 FIFO |
| 音频 | 1 | PCM FIFO 和输出缓冲 |
| 合计 | 43 | 应用、启动栈和帧缓冲位于外部 DDR |

BSRAM 数量取决于端口、位宽及写粒度，不能只按有效字节数推算。D-cache/TX 按字节写可能拆成多个 bank；CPU 多读端口寄存器文件会复制存储。USB PIO、UART、SPI 和 CSR 不占 BSRAM，但仍消耗寄存器/LUT RAM。

启动栈/data/BSS 保留区为 `0x407fe000..0x407fffff`，应用 SP 为 `0x40c00000`，应用预留 64 KiB 栈。没有地址 `0x10000000` 的集成 SRAM。默认 4 KiB L2 采用整条 128-bit refill 写，避免按字节拆分数据 RAM；8 KiB 配置尚未上板验证。

本页直接记录当前默认 full 的存储分配。相关实现见 [`gateware/soc.py`](../gateware/soc.py)、[`gateware/l2.py`](../gateware/l2.py)、[`gateware/audio.py`](../gateware/audio.py)、[`gateware/microphone.py`](../gateware/microphone.py)。修改配置后需重新统计，不能从逻辑字节数直接推导原语数量。
