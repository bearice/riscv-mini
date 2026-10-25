# AE350 用户 Flash / TWI 管理总线验收

2026-10-25，板载用户 U7 Flash 与 SYS_TWI/mux 已接通，最终同一候选通过完整 PnR、只读管理总线检查及 SD 50 MHz 回归。准确身份与原始回执见 [JSON](ae350-management.json)，接口与接线见 [管理总线](../docs/platforms/mega138k_ae350/management.md)。

SPI 使用硬核 ATCSPI200，经 fabric 三态 IO 接 U7。控制器 ID/revision 为 `02002047`，JEDEC=`EF4018`、容量 16 MiB、状态为 0，SFDP 签名 `50444653` 正确。配置 1 MHz mode 0，使用未对齐 buffer 对 offset `0x100` 的 512 B 重复读取，CRC 均为 `bd7bc39f`（该块读回为擦除态），guard 未损坏；容量边界外调用明确拒绝。未据此声明全容量内容或地址唯一性已验收，未写用户 Flash 或配置 Flash。

I2C 使用硬核 ATCIIC100（ID/revision=`02021011`），SCL/SDA 为开漏并接回输入。mux 的八种选择读回 mask=`ff`；raw2 的 EEPROM `0x50` 经 offset0 repeated-START 两次读取 16 B，CRC 均为 `b3441ce7`。对未命中地址 `0x55` 返回 NACK，越界选择 8 被拒绝。地址-only 探测中 `0x60` 在 raw3/raw4 ACK（mask=`18`），`0x50` 在 raw0/raw1/raw2 ACK（mask=`07`）；这只是地址响应，不表示全部下游设备功能已验收。没有编程 EEPROM 或时钟芯片。

GPIO raw selection bit0/1/2 对应 N19/P19/P26（硬 GPIO20/21/22），按命名引脚解释位序。测试后 I2C 被禁用、旧 mux 值和 GPIO 方向已恢复，SPI 空闲，FTDI 绑定恢复。

最终位流 `461f28416ba9`，管理载荷 `43572904acc9`。PnR setup/hold=0/0，最小脉宽裕量 +0.037 ns。SDK IO-port 包装组合的未定义模块问题通过硬核数字端口加 fabric 缓冲解决；控制器仍为硬核。收敛过程中对 SYSINFO 设备表做范围译码，SD 请求/响应增加缓冲，数据 FIFO 输出采用可选流水；20K 保持默认不启用该流水。

SD 回归曾检出缓冲响应后的重复 PHY 接收：PHY 已结束，Core 在消费缓冲的最后字节前仍保持请求 valid，可能再次启动同一响应接收，随后出现命令 CRC7 错误。增加每条命令单次门控，补充该竞态、默认/缓冲 FIFO 字节与 CRC 错误回归后，最终同位流 SD 50 MHz 九项测试全部通过，CRC 均为原基准 `1201fe31`，guard/错误/停止正常。失败实现和失败回执保留，没有将仅时序通过的候选当作整体验收。

验证为 SRAM/UART、轮询与只读访问；控制器工作期间不暂停 CPU。不包含 Flash 安装/XIP、SPI Quad/DMA、I2C 10-bit/中断/多主/故障注入或音视频长组合压力，不改 current。
