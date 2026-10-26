# AE350 HDMI 与 RGB565 控制台

> 适用机器：`mega138k_ae350`。配置以机器 JSON 和所选硬件产物的导出为准。

Tang Mega 138K Pro 的 HDMI TX 使用 TMDS0：时钟 Y22/Y23，数据 lane0
AB24/AC24、lane1 V24/W24、lane2 AA22/AA23。Dock 原理图标记为 DVI_TX；
另一组 K20/J20 时钟对应 HDMI RX。管脚与 3.3V/LVCMOS33D 约束来自本仓库
Board 描述，构建读取本地实现和本地 IP。

## 时钟与视频

模式为 640×480，水平总计 800、垂直总计 525；像素 25.000MHz、串行
125.000MHz，源帧率为 59.5238Hz。短测与 TTY 采集采用 YUYV 640×480/30fps，持续监控采用 MJPEG 640×480/60fps；
UVC 输出帧率不等于 HDMI 输入帧率，此采集接口未独立报告输入时序。
数字输出采用 DVI 兼容 TMDS。

已验收基线的系统/核心/数据/DDR CK 为 80/800/100/400MHz。数据时钟使用 PLL 静态
+50ps 下降沿微调，对应高/低 5.05/4.95ns 和 SDC 50.5% 占空比。
HDMI 串行器复位由低速 PLL 输出同步释放。SDK Flash 候选把相位从基线的
8ns 改为 10ns，避开 125MHz 快时钟的 8/12ns 边沿；释放间隔仿真通过，
50MHz sys 诊断候选的 hold/removal 已通过；100MHz 完整组合和板测仍待验收。
STA 保留 recovery/removal、setup/hold 与高低最小脉宽检查。

## DDR 与帧所有权

CPU 和 scanout 共用既有 256-bit DDR3 UI，按完整事务轮转。每次读拥有
接口直到真实返回；超时进入隔离并排空迟到响应。CPU 写命令与数据共同
握手，scanout 每条命令只读 32B。最大服务等待统计包含仲裁与实际读响应
延迟，单位为 100MHz UI 周期，计数饱和于 `0xffffff`。

scanout 描述符携带 base、stride、epoch 和 run。256-bit 返回拆成 RGB565
像素，经 2048 项异步 FIFO 进入像素域；每个像素带 epoch 和帧内索引。
配置通过一致快照跨域，在垂直消隐处一次应用。换帧会放弃尚未发出的旧帧
请求，在途读取先排空，FIFO 中的旧 epoch 在消隐期丢弃。

视频时序持续运行。缺少所需像素时，该帧剩余部分输出黑色并计错，下一帧
重新对齐。`UNDERFLOWS` 按坏帧计，`MISSING` 记录被遮黑的活动像素。
FIFO 最低水位由同步后的 Gray 写计数估计，为保守观察值。

停用仍发送 stop 描述符；只有像素端停用、DDR reader 停用且空闲后，
`STOPPED` 才成立。复位同时作用于视频 FIFO 的两端。测试图模式无需 DDR 读取。

## 控制页

地址 `0xf8030000`，ID `0xae350501`，ABI `0x00010000`（major 1）。capability 为 RGB565、双缓冲、
垂直消隐提交和内建测试图。SYSINFO 设备表从 `+0x120` 发布它。

| 偏移 | 寄存器 | 语义 |
| --- | --- | --- |
| 00/04/08 | ID/ABI/CAP | 只读身份 |
| 0c/10 | CONTROL/STATUS | 暂存 run/slot；当前 run、slot、pending、busy、STOPPED |
| 14/18/1c | IRQ_STATUS/IRQ_ENABLE/LAST_ERROR | IRQ字段为0；最近错误标志 |
| 20/24/28 | BASE0/BASE1/STRIDE | 暂存地址与每行字节数 |
| 2c | COMMIT | 写1提交完整快照，读为 pending |
| 30/34 | FRAMES/UNDERFLOWS | 物理帧计数、坏帧数 |
| 38/3c | ADDRESS_ERROR/TIMEOUT | 地址拒绝、读超时 |
| 40/44/48 | ACTIVE_BASE/PIXEL_HZ/GEOMETRY | 当前地址、频率、宽高 |
| 4c/50/54 | EPOCH/FIFO_MIN/MAX_WAIT | 帧代数、水位、最大 DDR 服务等待 |
| 58/5c/60 | READS/CPU_COMMANDS/MISSING | 命令数、被遮黑像素 |
| 64/68 | SEQUENCE/ACTIVE_SEQUENCE | 已提交/已应用序号 |
| 6c/70 | CANCELLED/ERRORS | 放弃旧帧、busy/非预期响应错误 |
| 74/78 | CLEAR_ERRORS/REQUEST_OVERFLOW | 写1清理软件提交错误；描述符丢失数 |

寄存器偏移只有 `address_map.py:VIDEO_REGS` 一份定义，RTL、生成 C 头与 BSP
共同使用。首32字节遵循平台公共控制头。

base 需32B对齐、位于 1MiB 以上的 DDR aperture；stride 需32B对齐、至少
width×2 且小于64KiB，完整帧不能越过 aperture。aperture 上限不是全容量
实板验收声明。pending 快照不会被覆盖；非法提交保留当前帧并记录错误。
`CLEAR_ERRORS` 清理提交侧地址/busy 错误，DDR 超时隔离状态仍须排空或协调复位。

## BSP 与 BIOS TTY

`firmware/bsp/mega138k_ae350/video/ae350_video.h` 提供 init、frame、present、
stop 与 status。init 验证双缓冲完整跨度和重叠；frame 只交出软件拥有的
缓冲。present 先按32B cache line clean，再提交并等待应用序号与实际地址
确认。停止或换帧超时后，缓冲仍可能由硬件持有。

TTY 复用 `firmware/bios/vt.c` 的 VT100/ANSI 状态机，AE350 构建配置为80列
30行，8×16单元。渲染器从设备读取几何与 stride，将脏单元绘制到后备帧，
保留每个缓冲自己的单元缓存。共享 VT 头的默认80×34保持原平台设置。

诊断使用 0x01000000 与 0x01100000 两块缓冲，避开 P5 的1/2/3MiB audio、
mic 和 CPU 压力区。使用前先整帧写入、clean/invalidate 后逐像素读回，
另有相邻 cache-line guard 检查。

## NixOS 操作

先在仓库根目录进入 `nix develop`，全部构建走 `scripts/mini.py`。
迭代时复用同一个 workspace，保留编译与 PnR 工作目录缓存；以下命令使用默认 workspace。

```sh
python scripts/mini.py build gateware --machine mega138k_ae350 --synthesize
python scripts/mini.py build payload --machine mega138k_ae350 --source firmware/diagnostics/mega138k_ae350/ae350_video_suite.c --optimize=-O2
python scripts/mini.py build payload --machine mega138k_ae350 --source firmware/examples/mega138k_ae350/ae350_hdmi_tty_demo.c --optimize=-O2
```

`board video-suite` 先校验位流/时序、SRAM装载和启动身份，再检查每张送达
USB 帧的六块色板。可用 `--payload` 和 `--cpu-jtag-location` 在采集结束后
核对诊断结果。TTY 使用 `--pattern console --expected-frame <RGB565>`，
按同一 BIOS VT/字体生成的画面比较文字掩码，容忍1像素压缩/缩放偏差。
每帧检查前景/背景采样，完整掩码每秒检查两次；保存 PNG 与统计。
静态图样不能识别重复帧，未送达的 HDMI 帧不能由此路径枚举。

`board audio-suite --video-capture-device /dev/video0` 配合带
`AE350_VIDEO_STRESS=1` 的 audio suite，记录共享 DDR 下 audio/mic、CPU 压力、
video 计数、水位和采集结果。UART 完成之前不暂停 CPU。
