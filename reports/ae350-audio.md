# AE350 P5 音频与麦克风验收

2026-10-22，Tang Mega 138K Pro，sys/mem/DDR CK 为 80/100/400 MHz。候选只下载 FPGA SRAM；Flash、catalog `current` 与版本未修改。接口与复现命令见[音频合同](../docs/ae350-audio.md)。

## 候选身份

硬件构建：`build/runs/20261022T094820517850Z-build-audio-mic-ack-hold-11124b/result.json`。

- Gateware artifact：`b527a0ae09e58f4e0c72cf923ade7e705e6a2b636e8201828a5a65687ddccb35`。
- FS SHA256：`d2c1e4ef461a1e6769ba74a6d2be47c9428e22f77571c8d450c5374794f963ba`。
- ROM 启动身份：`d68c52c23fdf`，自主 POST `600d0000`，DDR 初始化通过。
- 完整 PnR：setup/hold 违例 0/0，minimum pulse slack +0.131 ns。
- FPGA JTAG：USB Debugger `2025030317` A；UART：同设备 B；CPU JTAG：`serial:FactoryAIOT ProA`。编程前核对 FPGA ID；产物关联与启动身份不等于配置存储独立读回。

用户确认 mic DA=N18、CK=N26、LR=R16、WS=R17，PMOD0 由单只麦克风独占。板级描述同时给出 PT8211 BCK=E22、WS=F24、DIN=G24、PA_EN=H17。所有权、引脚碰撞、3.3 V 约束与机器 ABI 有拒绝检查。

源码复核计划 `build/runs/20261022T100947215411Z-plan-candidate-1f22b0/result.json` 确认复用上述硬件及 ROM，文档整理没有改变当前数字验收对应的硬件闭包。

## 已完成的数字检查

60 秒音频回执：`build/runs/20261022T095507020637Z-board-ae350-audio-suite/result.json`。载荷 artifact `9fa5b1d19b6d6d5c9c86f646486d188c08197154199eabc3ce5ddcdbd1fc0b64`。

| 检查 | 结果 |
| --- | --- |
| PIO、主动断供 | 256 帧；断供输出零并计数 |
| 停止/重启 | 100 次 PIO + 100 次在途 DMA；停止排空后写入不同代的已知样本 |
| mic snapshot | 512 个真实样本；非恒定输入；主动 overflow 后恢复 |
| busy 重提 / 真实 AHB 错误 | 分别返回 BUSY=11 / HW=12 |
| TX frames / played | 2,880,129 / 2,880,129 |
| RX accepted | 2,880,224，与 TX 时钟相符 |
| TX / RX 完整 DMA 块 | 各 1,875 块，每块 1,536 帧 |
| 稳态 underrun / overflow / 符号错误 / guard 错误 | 全部 0 |
| CPU 并发压力 | 独立 64 KiB DDR，561,784 次写入，数据缓存开启 |
| FIFO | TX 最低 511；RX 最高 1 |
| 实际 serializer 输入观察 | 8,483 次，8,307 次非零，越界 0 |

测试音低 16 位左声道 750 Hz、高 16 位右声道 500 Hz。内部 PCM 观察来自 serializer 实际装载值；它证明数字样本到达发送器，不能替代模拟插孔录音。

同一硬件的 DMA 回归 `build/runs/20261022T095312806742Z-board-ae350-dma-suite/result.json` 通过：10,000 次搬运无 mismatch/guard/timeout，链表/双通道/循环/取消通过，FIFO 4 次传输对应 4 次 ACK，满/空/复用/停止通过。ACK pulse catcher 的输出同时用于 REQ 门控，修正短 ACK 被合并计数的问题。cache schema `0x50330002` 强制同步后数值和 guard 正确；同步前旧行是否驻留只作观察。本次 `0x55`，不宣称验证脏行写回或缓存驻留。

同一硬件的 I/O 回归 `build/runs/20261022T095348862345Z-board-ae350-io-suite/result.json` 通过：1,000 次边沿/服务；DMA busy 时发生 282 次 fabric IRQ 服务，独立 CPU DDR 与 DMA 数据均正确。此次不重复签收 LED/按键/颜色物理观察，也不签 DMAC/audio/mic 的 CPU ISR。

长时测试后，同一硬件的独立映射核验 `build/runs/20261022T102942533886Z-board-ae350-audio-suite/result.json` 通过。逐 selector 开/关请求门的真实 FIFO 传输结果：audio on/off=0x240/0xc0，mic=0x440/0x40，loopback TX=0x41/0x40、RX=0x42/0x40。差集唯一对应 selector 9/10/0/1；此专用模式的终止码 34 与复用结果槽由 `judge_routes` 单独判定，不按正常播放计数解释。

## 30 分钟连续双 DMA

回执：`build/runs/20261022T095849048718Z-board-ae350-audio-suite/result.json`，载荷构建 `build/runs/20261022T094447734664Z-build-audio-mic-soak-51b263/result.json`，artifact `15538c9d38cb38fa8999fcf9aac65beedcad164497e755fa17eec6ab9cdfddb2`。18:59:24–19:29:24 JST 左右完成，数字合同通过。实时区间不 halt CPU；TX/RX DMA 与 CPU DDR 同时运行，结束后才停设备并读取最终结果。

| 指标 | 30 分钟结果 |
| --- | --- |
| TX frames / played | 86,400,129 / 86,400,129 |
| RX accepted | 86,400,224 |
| TX / RX 完整 DMA 块 | 各 56,250 块 |
| underrun / overflow / 符号错误 / guard 错误 | 全部 0 |
| CPU DDR 写入 | 16,849,846 次，数据缓存开启 |
| TX 最低 / RX 最高 FIFO 水位 | 511 / 1 |
| 实际 serializer 输入观察 | 262,984 次，256,937 次非零，越界 0 |
| PIO / 在途 DMA 停止重启 | 各 100 次通过 |
| 实时区间 debugger halt | 0 |

UART STREAM→DONE 主机窗口为 1800.011285 秒，serializer 计数折算 47,999.770731 frame/s，距 48 kHz 约 0.000478%，在 0.1% 目标内。窗口包含停止和串口报告开销，属于主机计时交叉核对，不是示波器频率测量。

该 operation 的 `uart.log` 和 `mic-peaks.json` 保存 180 个 10 秒窗口，最大绝对 PCM24 峰值 4,664,691，位于结束于第 490 秒的窗口。完整结构化汇总见 [JSON](ae350-audio.json)。

## 物理观察与待验收项

用户已确认在采集期间拍手或讲话。10 秒窗口输入峰值从初始约 6–9 万升至最高约 466 万；有明显幅度变化，但缺少精确刺激时刻和受控静音对照，因此保留用户已操作和客观幅度记录，不单凭此签收受控声学响应。

本机为 PCH / Realtek ALC662 rev3，用户确认主板只有一个物理音频接口。临时以显式 ALSA `hw:0,0` 采集，Capture 设为 0 dB 并开启。初始及稳态晚采样均未录到有效的左 750 Hz / 右 500 Hz 测试音；晚采样在 60 秒测试已运行约 38 秒时进行，不能归因为仅有上电瞬态。录音保存在该 60 秒 operation 的 `late-line-in.wav`。当前输入模式未确认适用于板上输出，因此模拟输出、声道分离、mute 衰减与主观听音均未签收。

用户于 2026-10-22 要求暂时跳过本机声卡模拟录音项，按已有数字验收结果整理提交。本次交付停止追查主机插孔模式与线缆条件；该项记为用户指定跳过，不计为通过。受控麦克风声学响应仍按上段的实际证据保留为未确认。

PT8211 数字边沿、声道和符号有 RTL 仿真；共享有理数时钟在 48/60/80/120 MHz 的平均帧率仿真为 48 kHz。实板帧计数除以主机 UART STREAM→DONE 时间另记实测值，不用主机录音采样率代替 FPGA 时钟测量。运行期间无新增音频时钟域。

主机/RTL 回归包括 AE350 AHB/APB FIFO 合同、签名/格式/快照/overflow、板级绑定和 ABI、DMA/音频结果判定、Linux JTAG 选择、20K 音频/麦克风核心与兼容录音工具、构建源闭包。历史失败回执保留。P5 数字实时链路通过；本机声卡模拟录音按用户要求跳过，物理观察的限制保留，不宣称全部物理验收通过。

## 收尾状态

已做的输入模式核对：`Rear Mic / Pink` 是 codec 驱动标签，不能据此认定物理接口的位置或颜色。实际 ADC 路由为 0x08→0x23→0x18；pin 0x18 的 `Pin-ctls=0x24: IN VREF_80`，目前为带偏置的 Mic 输入配置，尚未确认 Line In。ALSA mixer 没有公开 Line In / Input Source 切换项；`hw:0,0` 只选择 PCM 设备，并不会切换插孔模式。因此上述录音结果只说明当前输入路径没有收到有效测试音，不能判定 FPGA 模拟输出故障。进一步的单接口能力核实随本项一并跳过。

本机 Capture 恢复为原始 0/0、off/off；声卡 control/capture 节点 ACL 按保存文件恢复并读回一致。两只 FTDI 的 JTAG interface A 已恢复绑定 `ftdi_sio`，UART B 始终保持绑定。收尾记录为 `build/audio-host-state/restored.json`。

板上保留本候选 SRAM 位流，映射核验载荷已完成；TX 停止静音、mic 禁用，CPU 在结束 idle loop。未写 Flash、未提升 `current`。源码提交以 Git 历史为准；本记录中的构建和板测来自上述开发候选。
