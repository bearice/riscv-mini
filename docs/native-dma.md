# 当前 DMA 内存路径

默认 full 使用共享 writeback L2，CPU/音频为 32-bit Wishbone，LCD 和 SD lite 为 128-bit coherent streaming。实现见 [`soc.py`](../gateware/soc.py)、[`shared_l2.py`](../gateware/shared_l2.py)、[`native_dma.py`](../gateware/native_dma.py)。

| 客户端 | 接口 | 行为 |
| --- | --- | --- |
| RGB LCD | 128-bit streaming read | 双帧槽，读取当前显示帧，FIFO 吸收 DDR 延迟 |
| SD lite | 128-bit streaming read/write | 32-bit 字节转换后按四字打包，最多 4 KiB；读写通过 NativeDMAArbiter 共用 L2 的 SD 入口 |
| SD full | 32-bit Wishbone DMA | 通用读写引擎，保留较大传输配置；HAL 仍按最多八扇区分块 |
| SD SPI | CPU 软件读写 | 没有 DDR DMA |
| 音频 DAC | 32-bit Wishbone read | PCM ring，平均 48 kHz，带宽低，保持现有 HAL |
| Ethernet | CPU PIO packet SRAM | MAC 收发 FIFO/SRAM，未实现 DDR packet copy DMA |
| USB Ultra | CPU PIO | 小包 HID/Hub，未实现 DDR DMA；可选 OHCI 后端另有自己的描述符 DMA |
| 双麦克风 | snapshot FIFO + CPU | 不使用连续 DDR DMA |

## 所有权与约束

共享入口的读能看到 L2 dirty 行，SD 写命中会更新 dirty 行，未命中提交 DDR。写入确认在事务提交后返回，不采用提前确认的 posted write。帧缓冲区不分配缓存行。

SD 使用 16 字节对齐、4 KiB DDR bounce buffer，上层可提供非对齐缓冲。HAL 所有权交接仍执行 fence 和私有 L1 D-cache invalidate，见 [L2](l2-cache.md)。停止会排空已经接受的请求；长度、对齐和地址错误返回错误，不能用关闭使能撤回已经提交的 DDR 写。

无 L2 配置为独立 LiteDRAM crossbar 端口；有 L2 时 LCD 和 SD 经共享缓存入口，不再使用自定义全局 DDR scheduler。控制器 crossbar 的 bank 仲裁仍存在。

`sim/test_native_dma.py` 检查打包、尾部掩码、停止、延迟后端和 SD 读写仲裁。生成 Verilog 的打包掩码回归见 `tests/native_sd_verilog_test.py`。实板使用 `test dma`、SD 文件读写、LCD 整帧 CRC、音频欠载/错误计数及外部网络并发检查；这些检查不代替声道听音或任意设备组合的验收。
