# AE350 原生 SD bring-up

2026-10-24：P7 的首个只读门禁通过，整阶段尚未完成。LiteSDCard 原生 PHY/Core 连接 4-bit 引脚，以 AHB FIFO 和硬核 DMAC 访问既有 DDR 通路。当前实现与操作见 [SD](../docs/platforms/mega138k_ae350/sd.md)，准确身份和原始回执见 [JSON](ae350-sd.json)。

完整 PnR setup/hold 为 0/0，最小脉宽裕量 +0.037 ns。板端 ITCM POST/DDR 初始化通过；SD 检出 31,116,288 个 512-byte 扇区，在 10 MHz、4-bit 下通过 SCR 和扇区 DMA 读取。对齐单扇区和未对齐连续八扇区的首扇区 CRC32 均为 `732fda42`；八扇区 CRC32 为 `37edb163`，两侧 guard 保持 `a5`，无 CPU trap、协议错误或排空失败。这些 CRC 是同卡内部重复读观察，未和独立主机参考比对。

RX REQ[5]→selector 12 通过真实卡数据传输。TX REQ[4]→selector 11 通过请求门关闭时 DMA 等待、开启后完成 32-byte DDR→TX FIFO、FIFO 水位 8 和通道排空验证；没有发送 CMD24/CMD25，不表示写卡已验收。

首次候选在 CMD0 事件观察阶段失败。原因是手工 APB 适配只使用了复合 CSR 的 storage/status 信号，遗漏 CSRField 聚合模块。修正按 LiteX GenericBank 方式 finalize 并持有 CSR 模块，补充命令字段和事件聚合回归后，重新全量 PnR 与本次实板通过。失败回执保留。

主机检查通过：SD CSR 字段/事件回归、实际 LiteSDCard 字节转换/背压/复位尾部、SD 结果合同负例、现有平台互连/音频以及 11 项 DMA 结果合同测试。编译与板测载荷均为独立 AE350 产物。

全程只写 FPGA SRAM 和 UART 装载内存，不写 SD/Flash、不修改 current。两只 FTDI JTAG A 接口已恢复原驱动绑定，SD 请求门关闭、DMAC 通道排空。

尚未完成：独立主机扇区 CRC、FatFs 与专用测试文件 1/16/64 MiB 写读、BIOS 文件服务/运行时 ABI、选定应用、audio+HDMI+SD 30 分钟组合验收。
