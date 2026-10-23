# AE350 音频与麦克风

> 适用机器：`mega138k_ae350`。配置以机器 JSON 和所选硬件产物的导出为准。

PT8211 TX 和单只 I2S RX 复用 `gateware/devices/` 协议核心。AE350 使用 APB 控制页、固定地址 AHB FIFO 数据口和硬 DMAC。运行点为 sys 80 MHz、mem 100 MHz、DDR CK 400 MHz；板测结论以匹配构建的 `board audio-suite` 回执为准。

## 接线与格式

唯一引脚来源是 `gateware/boards/mega138k_pro/board.json`。输出 BCK=E22、WS=F24、DATA=G24、PA_EN=H17。用户于 2026-10-22 确认 PMOD0 麦克风 DA=N18、CK=N26、LR=R16、WS=R17；DA 输入，其余输出。PMOD0/J28/SSPI 的当前所有者为麦克风，禁止重叠分配，IO 为 LVCMOS33。

TX 每个 32 位字低 16 位左、高 16 位右，先发右（WS=0）、再发左（WS=1），每帧 32 BCLK。RX 是标准 I2S，每帧 64 BCLK，WS 后延迟一位，接收 24 位并符号扩展为 int32_t；LR 选择左/右时隙。当前只发布一只麦克风。

PT8211 在 BCK 上升沿接收二补码、MSB 先发，WS 低/高分别选择右/左声道；格式依据厂商 [PT8211 V1.7 §Function Description](https://www.princeton.com.tw/Portals/0/activeforums_Attach/PT8211%20V1.7_E.pdf?ver=I8v0SNPIQri_LxlOI2sp7w%3D%3D)。发送器在下降沿更新 DIN/WS，供下一个上升沿采样。

共享有理数时钟使能产生平均 48,000 frame/s、TX BCLK 1.536 MHz、RX BCLK 3.072 MHz。逻辑均在 sys 域，不新增 PLL；边沿量化误差小于 12.5 ns，三帧合计 5000 sys 周期。

## CSR 和 FIFO

TX 页 `0xf8010000`、RX 页 `0xf8020000`，各实现前 4 KiB，采用公共设备头，仅允许对齐的完整 32 位 APB 写。TX DATA 是固定地址 `0xe8000000` 写口，RX DATA 是 `0xe8010000` 读口；错误方向、尺寸、保留地址、满 TX 或空 RX 返回有界 AHB ERROR，不提交样本。CSR 读取不出队。

| 偏移 | TX | RX |
| --- | --- | --- |
| 0x00/04/08 | ID / ABI / 能力 | ID / ABI / 能力 |
| 0x0c | run bit0、mute bit1、DMA bit2；写 bit3 clear | enable bit0、right bit1、stream bit2、DMA bit3；写 bit4 arm、bit5 clear |
| 0x10 | writable、功放、REQ、ACK、hold | busy、done、activity、REQ、ACK、hold |
| 0x14/18/1c | IRQ W1C / enable / sticky error | IRQ W1C / enable / sticky error |
| 0x20/24/28 | rate / level / depth | rate / level / depth |
| 0x2c/30/34 | frames / played / underruns | clocked samples / captured / overruns |
| 0x38/3c | core overrun count / last word | last sample / activity |
| 0x40 | exported REQ wire | exported REQ wire |
| 0x44 | DMAC selector（9） | accepted stream samples |
| 0x48 | reserved | DMAC selector（10） |

FIFO 各 512 帧，约 10.67 ms。TX 缺样输出零并计数；pause 不消费，mute 继续消费并关闭功放。RX 快照收满 512 个样本停止写入；stream 满时丢弃新样本并计 overrun，腾空后继续采集。TX IRQ bit0=underrun、bit1=DATA 拒绝；RX bit0=overflow、bit1=snapshot done、bit2=DATA 拒绝。新事件优先于同拍 W1C，snapshot done 不计 LAST_ERROR。TX/RX 使用 EXT_INT[2]/[3]，PLIC source 另行发现。

## BSP 与所有权

`firmware/bsp/mega138k_ae350/audio/ae350_audio.h` 提供 PIO、快照、stream、DMA 提交/查询和停止。设备由单调用者串行使用，调用者选择并独占 DMAC 通道。REQ/ACK 只握手，AHB 数据相才改变 FIFO。

DMA buffer/长度必须完整覆盖独占的 32 字节 cache line，位于有效 DDR；DMA 期间 CPU 不接触。TX 提交前 clean，RX 交回 CPU 前 invalidate。busy 重提在改写通道前拒绝；只有真实 TC 且无 error/abort 才算完成。停止先撤销 REQ，再 abort/drain，最后清 FIFO；停止失败时 buffer 继续有效。公共 API 不复位其他 DMA 用户；独立 suite 自行初始化整个 DMAC，并打开数据缓存验证所有权。外部 REQ[2]/[3] 的 DMAC selector 为 9/10，两者不可混用。

## 验收入口

```sh
python scripts/mini.py build gateware --machine mega138k_ae350 --synthesize
python scripts/mini.py build payload --machine mega138k_ae350 \
  --source firmware/diagnostics/mega138k_ae350/ae350_audio_suite.c --helper string \
  --define AE350_AUDIO_SECONDS=1800
python scripts/mini.py board audio-suite --build <gateware-result.json> --payload <payload-result.json> \
  --port <UART> --cpu-jtag-location <CPU-selector> --soak-seconds 1800 --timeout 1900 \
  --capture-device <host-input>
```

Linux CPU selector 为 `serial:<FTDI interface serial>`；录音使用显式 ALSA `hw:0,0` 等设备，需要 `arecord`、设备权限和可用的 Capture mixer。Windows 沿用 D2XX location 和 WinMM 输入名。Linux JTAG interface A 操作前解除 `ftdi_sio` 绑定，UART B 保留；结束后恢复。

Suite 检查 PIO/主动断供、100 次重启、512 样本快照、主动 overflow、busy 重提、真实 AHB 错误传播、双 DMA 与 CPU DDR 并发。持续时间按音频帧计数；实时区间不 halt CPU，UART DONE 后才读取结果。

停止覆盖 100 次 PIO 与 100 次在途 DMA；取消后排空通道并清 FIFO，再写入不同代的已知样本以排除旧队列残留。每个 DMA block 为 1536 帧；CPU 同时访问独立的 64 KiB DDR。10 秒输出一条 mic peak，不向麦克风采集 buffer 注入软件测试数据。`AE350_AUDIO_ROUTE_SCAN=1` 构建的专用载荷配合 `--qualify-routes` 可逐 selector 核对四个真实 FIFO 请求；该模式不是长时音频验收。

30 秒以上的回执另存 serializer 帧计数与主机 UART STREAM→DONE 时间之比，包含停止和串口报告开销；短测试不据此判断 0.1% 的晶振误差。准确候选、长时结果与物理限制见[验收记录](../../../reports/ae350-audio.md)。

测试音左 750 Hz、右 500 Hz，峰值约 1024/32768；先输出两秒、mute 两秒、再恢复。主机保存四秒录音和频率/通道/削波/mute 分析；声卡采样率不自动代表 FPGA 实际采样率。麦克风非恒定样本与 activity 不自动计为受控声学刺激或主观听音通过。候选只下载 SRAM，不写 Flash/current。
