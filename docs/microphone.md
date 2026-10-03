# I2S 麦克风与 LCD 波形

模块标记为 `ME27 X916`，尚不能据此确认芯片型号。接收器采用标准 I2S：
24 位有符号 PCM、每声道 32 个 BCLK、每帧 64 个 BCLK，WS 变化后延迟一位
再传 MSB。协议参考 [Sipeed I2S 麦克风资料](https://dl.sipeed.com/MAIX/HDK/Chip_DS/%E9%BA%A6%E5%85%8B_MSM261S4030H0%28%E4%BD%BF%E7%94%A8%E7%9A%84%29.pdf)，
该资料不表示实际模块已识别为其中的型号。

| 模块信号 | FPGA 引脚 | 方向 |
| --- | --- | --- |
| DA | P11 | 输入，弱下拉用于未选中声道的高阻间隔 |
| CK | R11 | BCLK 输出，默认 DDS 平均 3.072 MHz（legacy 3 MHz）；无内部上下拉 |
| LR | M15 | 输出，0=左、1=右；同步选择接收声道；无内部上下拉 |
| WS | J16 | 输出，默认 DDS 48,000 Hz（legacy 模式 46,875 Hz）；无内部上下拉 |
| G / V | GND / 3V3 | 电源 |

以上接线由用户确认：P11/R11 位于 PG256 Bank 2，M15 位于 Bank 1，
J16 位于 Bank 0，当前 VCCIO 均为 3.3 V，使用 LVCMOS33，没有已分配功能冲突。
WS2812 仍独立使用 T9。第二只麦克风接 DA=T6、CK=R8、LR=T8、WS=P9，
使用 Bank 3 的 LVCMOS33。

CPU/sys 60 MHz 以 clock-enable 分频生成 BCLK，WS 在 BCLK 下降沿变化，
输入经过两级 sys 同步后在 BCLK 上升沿采样。没有新增 PLL 或时钟域。
启动后需给模块至少 200 ms 稳定时间，停止后 BCLK/WS 回到低电平。

接收器持续产生时钟；`capture` 只采集下一组 512 个样本（48 kHz 下 10.667 ms），
完成后停止写 FIFO，但保持时钟。FIFO 是 48×512 位 BSRAM，CPU 经 CSR
读取并符号扩展为 int32_t。读取完成才能再次 arm；当前不提供连续录音 DMA，
绘图/等待期间的声音不会存入下一快照。

双路模式使用同一个时钟发生器驱动两组 CK/WS，第一只 LR=0、第二只 LR=1。
两根 DA 分别同步和移位，先保存左时隙，再在同一帧右时隙结束时把一对样本
写入 FIFO；arm 时丢弃不完整的一对，避免窗口第一对混入上一帧的左样本。
`hal_mic_start_stereo()` 启用此模式，`hal_mic_read_stereo()` 读取
`hal_mic_pair_t { int32_t left,right; }`；`level/captured/samples` 计数单位为样本对。
单路模式仍由 `hal_mic_start(right)` 选择第一只麦克风的 LR/时隙，仅使用 FIFO
低 24 位；单路和双路读取 API 检查模式，避免错误解读。

独立双路程序 `firmware/examples/microphone_stereo_demo.c` 在上方绘制绿色左路
（P11/R11），下方绘制青色右路（T6/R8），每路 512 点 / 10.667 ms，两个
图使用共同增益并各自去除均值。`A` 自动增益、`+/-` 手动增益、空格冻结。
串口提供 `status`、`perf`、`test mic stereo`（`test mic` 为别名）、`dump`（512 对
十六进制 PCM24）、`hold`、`stop`、`run`、`reboot`。基础 monitor 也提供
`test mic stereo`，完成后关闭时钟；两个输入必须非恒定、有 DA 活动，并有
不同的样本值。该检查不能单独证明声学声道归属，需分别对两只麦克风发声确认。

HAL 提供 `hal_mic_start(right)`、`hal_mic_stop()`、`hal_mic_capture()`、
`hal_mic_read()` 和 `hal_mic_get_info()`。monitor 提供 `test mic`，检查
512 个样本、PCM24 范围、数据非恒定及 FIFO 错误，之后关闭麦克风。

独立 demo 使用 DDR 双帧缓冲，448 像素宽的绿色曲线显示最新快照，移除
均值后绘图；页面同时显示峰值、峰峰值、快照数、错误数和 LCD 欠载。
默认自动增益会放大底噪，判断实际声强应结合 PEAK/P-P 数字；USB 键盘
`A` 切换自动增益，`+/-` 调整手动增益，空格冻结画面，`L/R` 切换声道。

```powershell
.venv/Scripts/python.exe scripts/build.py --output-dir build/mic --synthesize
.venv/Scripts/python.exe scripts/build.py --app firmware/examples/microphone_stereo_demo.c --output-dir build/mic-demo
.venv/Scripts/python.exe scripts/boot_upload.py --program --mode uart --output-dir build/mic --image build/mic-demo/firmware/app.img
```

麦克风 CSR（含输入活动标志）属于镜像 ABI，FPGA 配置与应用必须匹配；
软件复位恢复已安装的 Flash 应用。更改配置时应同时更新匹配镜像。更改配置后必须重新检查时序/资源；仿真或非恒定数据本身不代表实物声音响应已通过。

`DA HIGH` / `DA_HIGH_SEEN` 表示从启动/clear 起，同步后的 DA 曾出现高电平。
它观察所有时隙，独立于 PCM 解码：若它为 0 且样本全零，应先检查模块供电、
接线和时钟到达；若它为 1 而 PCM 全零，再检查声道/格式。该标志不证明
声音有效，也不测量电压。
