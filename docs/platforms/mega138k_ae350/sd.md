# 原生 SD 与硬核 DMAC

适用机器：`mega138k_ae350`。控制器复用锁定依赖中的 LiteSDCard `SDPHY` 与仓库 `gateware/vendor/litesdcard/sdcore.py`，以原生 1-bit 初始化，再协商 4-bit SDR。当前 BSP 只开放只读 bring-up；FatFs、文件写入、BIOS 文件服务及音视频/SD 组合验收尚未交付。

## 接线与数据通路

Dock 4071f 原理图的 SDIO/M.2 页（J19、R189–R195）及本地板卡示例约束是引脚依据，绑定由 Board/Machine JSON 生成：

| 信号 | FPGA 引脚 | 电气 |
| --- | --- | --- |
| CLK | D23 | LVCMOS33 |
| CMD | D24 | LVCMOS33，上拉 |
| DAT0 / DAT1 / DAT2 / DAT3 | H23 / J23 / A25 / B25 | LVCMOS33，上拉 |
| DET | J15 | 低有效，有卡为 0 |

SD 控制器运行在现有 100 MHz mem 域，APB/AHB 保持 sys 80 MHz，通过保留事务的异步 FIFO 完成跨域；REQ 在 sys 域被 ACK 立即抑制，ACK 以事件同步回 mem 域。

数据路径：SDPHY/Core ↔ 字节/32-bit 转换与双向 FIFO ↔ Extended AHB 固定地址端点 ↔ 硬核 DMAC ↔ 既有 DDR AHB 通路。SD 不增加独立 DDR 主端口，不使用 LiteSDCard 的 Wishbone 内存 DMA 前端。FIFO 每方向 16 个 32-bit 寄存器字，字节顺序按小端内存转换；AHB 只允许对正确方向的固定数据字做 32-bit 访问，空读/满写及非法地址有界 ERROR。请求按 FIFO 可用性产生，ACK 捕获与 audio/mic 相同。

| 接口 | 地址/编号 |
| --- | --- |
| APB 控制页 | `0xf8040000`，4 KiB |
| AHB TX DATA / RX DATA | `0xe8020000` / `0xe8020004` |
| TX REQ / RX REQ | 4 / 5 |
| 候选 TX selector / RX selector | 11 / 12，见下方验收范围 |

REQ 线号不等于 DMAC selector。当前 RX 映射经真实读卡 DMA 验证，TX 映射经请求门关闭/开启与 TX FIFO 传输验证；TX 测试不发送写卡命令，不能视为介质写入通过。详见 [验收记录](../../../reports/ae350-sd.md)。硬 DMAC 通道由调用者分配，BSP 初始化参数指定通道，生命周期内须独占；不占用 audio/mic 的通道。软件枚举以 SYSINFO 最后一个设备表条目和准确镜像 ABI 为准。

## 控制寄存器

所有偏移相对 `0xf8040000`，32-bit 小端。除说明可写外均只读。

| 偏移 | 含义 |
| --- | --- |
| 0x00 / 0x04 / 0x08 | DEVICE_ID=`0xae350801` / ABI / CAPABILITIES=7 |
| 0x0c | CONTROL，bit0 本地复位、bit1 DMA 请求门，可写 |
| 0x10 | Core busy |
| 0x20 | SD 控制器源时钟频率（当前 100 MHz） |
| 0x24 / 0x28 | TX / RX FIFO 字数 |
| 0x2c | TX selector [7:0]、RX selector [15:8] |
| 0x40 | card detect，0 表示有卡 |
| 0x44 / 0x48 / 0x4c | PHY divider / settings / init pulse，可写 |
| 0x50 / 0x54 | 命令/数据超时周期，可写 |
| 0x80 / 0x84 / 0x88 | 命令 argument / command / send，可写 |
| 0x8c–0x98 | 128-bit response，从高字到低字 |
| 0x9c / 0xa0 | command / data event，done/error/timeout/CRC |
| 0xa4 / 0xa8 | block length / count，可写 |

CSR 页没有 FIFO DATA 别名。PHY 时序配置在 core busy 时拒绝修改；停止须先关请求门，abort/disable 本次拥有的 DMAC 通道并确认排空，之后才能复位 SD FIFO 或复用内存。排空失败时保留所有权并返回错误。

## 只读 BSP 与诊断

`ae350_sd_init(channel)` 在约 400 kHz 初始化，读取 CSD/SCR 后协商 4-bit 并切换到 10 MHz；实际频率按本设备源时钟与 divider 回读计算。`ae350_sd_set_clock(hz)` 可选择不超过源时钟一半的速度，最高 50 MHz；超过 25 MHz 前必须完成 CMD6 High Speed 查询、切换与返回选择结果核验。当前卡已通过 50 MHz 短时只读测试，见 [频率对照](../../../reports/performance/ae350-sd-frequency.md)。`ae350_sd_read(lba, buffer, count)` 以最多 8 扇区拆分 CMD17/CMD18，CMD18 后 CMD12；调用者 buffer 可不对齐。DMA 只访问独立的 32-byte 对齐 DDR bounce buffer，交给设备前 clean，交还 CPU 前 invalidate；按非一致 DMA 使用。

```sh
nix develop --command python scripts/mini.py build gateware --machine mega138k_ae350 --synthesize --purpose sd-native-dmac
nix develop --command python scripts/mini.py build payload --machine mega138k_ae350 --source firmware/diagnostics/mega138k_ae350/ae350_sd_suite.c --purpose sd-readonly
nix develop --command python scripts/mini.py board sd-suite --build <gateware-result.json> --payload <payload-result.json> --port <UART> --cpu-jtag-location <CPU-JTAG>
```

相同目标始终复用默认 workspace。诊断仅读 LBA0 和 LBA0–7，比较对齐/未对齐读取的首扇区 CRC、检查 guard，并记录容量、总 CRC 和实际总线模式。`--expected-sector-crc <CRC32>` 可传独立主机参考；未传时 `host_reference_matched=null`，仅表示内部重复读取一致。MBR 签名只观察，不要求卡必须采用 MBR。

板测只编程 FPGA SRAM，经现有 UART loader 装载载荷，不写 Flash 或 SD。PnR、内部读一致、独立主机 CRC、文件系统和整阶段验收分别判定。

## 读取速度

`ae350_sd_benchmark.c` 使用相同只读 BSP，分别以 512 B、4 KiB、64 KiB API 调用读取 LBA 2048 起的 1 MiB，每种调用大小三轮。驱动内部最多每次传输 8 扇区，因此 64 KiB 调用仍拆为 4 KiB CMD18。计时包含命令、硬 DMAC、缓存同步和 bounce copy，CRC 与 UART 格式化在 CPU 周期计时区外；每轮检查相同内容 CRC 和两侧 guard。

```sh
nix develop --command python scripts/mini.py build payload --machine mega138k_ae350 --source firmware/diagnostics/mega138k_ae350/ae350_sd_benchmark.c --purpose sd-read-speed
# 对照组额外传 --define SD_BENCH_CACHE=3，开启 CPU I/D cache；SD 时钟仍为 10 MHz。
nix develop --command python scripts/mini.py board sd-suite --benchmark --build <gateware-result.json> --payload <benchmark-result.json> --port <UART> --cpu-jtag-location <CPU-JTAG> --timeout 600
```

测速模式在 UART 完成标记之前不打开 CPU 调试接口、不暂停 CPU。保存每轮原始 `rdcycle64`、主机单调时钟 START/END 时间及内容校验，结束后才通过 JTAG 核对完成和停止状态。主机时间包含 START/END 行长度差及 USB 调度带来的毫秒级开销；它用于核对 CPU 周期频率，短窗口提频比较采用经长窗口核对的周期计时。无 FatFs 文件缓存或音视频并发。结果见 [10/25/50 MHz 对照](../../../reports/performance/ae350-sd-frequency.md) 与 [初始读取性能](../../../reports/performance/ae350-sd-read.md)。

提频测速构建额外传 `--define SD_BENCH_HZ=50000000`。已知基准内容时可传 `--define SD_BENCH_REFERENCE_CRC=<CRC32>`；本卡同区间使用 `0x1201fe31`。初始化默认仍为 10 MHz，应用显式选择更高速度。
