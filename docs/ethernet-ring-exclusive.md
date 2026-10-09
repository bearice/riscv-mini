# Ethernet DMA 独占模式验收

2026-10-09，`codex/eth-ring-dma-no-fpu`：启用 Ethernet DMA 的配置只保留 descriptor ring 收发路径，HAL 同时禁用相应 PIO 代码。本文记录提交前的独占 ring 阶段验收；后续 U-Boot 驱动、默认 profile 和组件提交见 [U-Boot ring](uboot-ethernet-ring.md)。

## 配置与行为

DMA 构建中的 MAC packet SRAM 不再映射到 CPU/system 总线。DMA 的 SRAM 访问使用私有 Wishbone 连接；DDR 描述符和包数据继续通过与 SD 相同的 buffered native 内存端口传输。MAC SRAM 暂存仍存在。

MAC RX 队列释放、TX 命令及 slot/length 由 ring 独占控制。停用 ring 时也不会切回旧 CSR 控制；旧 TX start/slot/length 寄存器及 MAC IRQ 不再出现在该配置的接口中。PHY 管理、错误计数和只读队列状态继续用于初始化及诊断。

HAL 的 PIO 搬包循环、旧 MAC IRQ、`tx_busy/tx_slot` 只在关闭 DMA 的独立配置中编译。旧 CPU 逐包启动的 copy-DMA 辅助函数和对应诊断已删除。DMA 配置缺少 ring 后端时编译失败，避免配置不一致时选择 PIO。`hal_eth_send/receive` 在 DMA 配置下只使用 ring；调用方缓冲与 DDR ring 之间的数据复制用于异步缓冲寿命，不访问 MAC packet SRAM。

CPU 初始化 RX/TX 环后，硬件自动搬运、发布完成。环满或停机采用 ring 的背压/停止语义，没有 PIO 回退。

## 最终构建

目录：`build/ring-no-pio-final`。ID：`5865db4-full-rv32ima-rom8k-l24k-eth-ring-exclusive-no-pio-qualified-64fdcb48-f8542fdb`。

RV32IMA、无 C/FPU、MMU、ROM8K、L2 writeback 4K，CPU/L2 60 MHz、DDR 120 MHz；SD lite native、USB ultra、LCD、音频 DDS、双麦克风全部启用；place=2/route=2。

- 源码指纹：`76f10b848b7774e8dbf407ef541f15137fb12c5bfdf5da75114dcc058268f595`。
- FS SHA256：`64fdcb485e925d894211c5ea257eafc55741f81379a6ecd010aab3608071cd12`。
- app SHA256：`e920a2344c27c39de9545e70234660598dc7c407980c81acd07b4ee24860dac5`。
- CSR ABI：`4e9d42fa`，固件必须和本次 gateware 配套。
- PnR：setup/hold 均为 0。

| 资源 | 上一版 ring | DMA 独占版本 | 变化 |
| --- | ---: | ---: | ---: |
| Logic | 18,707 | 18,885 / 20,736 | +178 |
| Register | 10,405 | 10,370 / 16,173 | -35 |
| CLS | 9,999 | 10,120 / 10,368 | +121 |
| BSRAM | 42 | 36 / 46 | -6 |

当前剩余 248 CLS、10 BSRAM。删除兼容接口后的综合与布局结果并非所有资源都减少；上表仅对比同配置的实际实现结果。

## 验证结果

- 配置、buffered ports、shared L2、emitted ring RTL 和 native payload copy RTL 回归通过。
- MAC 队列接口验证通过：ring 停用时旧 CSR TX/RX 控制不能取得队列所有权，ring 握手仍有效。
- 生成接口检查通过：无 CPU packet SRAM 区域、旧 MAC IRQ、PIO TX 命令。HAL 目标文件没有旧 PIO 状态、中断处理或逐包 copy-DMA 辅助函数；错误的 DMA/ring 配置被编译期检查拒绝。
- 关闭 DMA 的独立全配置完成固件编译和 RTL 生成；此 PIO 控制配置未重新 PnR 或板测。
- 最终 FS/app 匹配 50 项固件板测；60 秒、35 轮 SD/LCD/USB/audio soak 通过，LCD underflow、音频 underrun/error 均为 0，双麦克风通过。
- ARP、4 次 ICMP ping 通过。CPU 暂停收包处理 1 秒，RX producer 从 `0x000b` 到 `0x000f`，consumer 保持 `0x000b`，4 个 UDP 帧恢复后按序正确回包。
- 并发网络：30.68 秒、110 个 UDP 包、37,983 字节 payload，长度 0..1472 字节，零超时，最大 RTT 4.42 ms；音频 underrun/error 和 LCD underflow 为 0。
- `all/cache` 各三轮通过，测试后 BIOS/audio 恢复通过。相对上一版 ring，CPU mul-add -0.1% cycles、L2 read -0.1% cycles、SD 4K +0.1% MiB/s；cache 压力测试累计 LCD underflow 两版均为 24，普通并发均为 0。

汇总：`build/ring-no-pio-results.json`；板测/恢复：`build/ring-no-pio-evidence`；性能：`build/ring-no-pio-performance`。构建目录保存 dirty 源码 recipe、patch、未跟踪输入和 CPU RTL；最终源码指纹匹配。前期中间构建不作为本次板测证据。

测试结束已用 SRAM/UART 恢复正式 `current v0.8.0`，启动通过。无 Flash 写、current 变更、提交、合并或推送；主工作树 clean，旧参考分支不变。

尚未测满 100 Mbps 或长期突发压力；没有新增物理屏幕、听音或键鼠输入观察。
