# BSRAM 资源核对

适用机器：`primer20k_vexriscv`。BSRAM 使用量随 CPU 特性、ROM/L2 容量、设备和后端变化，从本次生成 RTL 与 PnR 报告统计，不保留固定 full 配置总数。

| 模块 | 主要存储来源 |
| --- | --- |
| CPU | 寄存器文件、I/D-cache、可选浮点寄存器文件 |
| 启动存储 | ROM 后端的指令内容；启动栈/data/BSS 复用 L2 |
| L2 | 数据、tag、valid/dirty 状态 |
| 视频 | 跨域/扫描 FIFO、可选调色板；帧缓冲位于 DDR |
| Ethernet | MAC packet SRAM、CDC FIFO；ring 描述符和载荷位于 DDR |
| SD / 音频 / 麦克风 | 设备 FIFO 与流缓冲 |

原语数量取决于端口、位宽、写粒度和综合推断，不能用逻辑字节数直接相除。按字节写可能拆 bank，多读端口可能复制存储；未使用 BSRAM 的模块仍可能消耗寄存器或 LUT RAM。

修改配置后同时检查本次 PnR 的 Logic、Register、CLS、BSRAM、PLL 和时钟网络资源，并验证时序。旧布局余量不作为新配置可容纳性的依据。内存结构见 [L2](l2-cache.md) 与 [DDR 启动](ddr-boot.md)。
