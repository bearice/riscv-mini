# I2S 麦克风与 LCD 波形

模块标记为 `ME27 X916`，尚不能据此确认芯片型号。接收器采用标准 I2S：
24 位有符号 PCM、每声道 32 个 BCLK、每帧 64 个 BCLK，WS 变化后延迟一位
再传 MSB。协议参考 [Sipeed I2S 麦克风资料](https://dl.sipeed.com/MAIX/HDK/Chip_DS/%E9%BA%A6%E5%85%8B_MSM261S4030H0%28%E4%BD%BF%E7%94%A8%E7%9A%84%29.pdf)，
该资料不表示实际模块已识别为其中的型号。

| 模块信号 | FPGA 引脚 | 方向 |
| --- | --- | --- |
| DA | P11 | 输入，弱下拉用于未选中声道的高阻间隔 |
| CK | R11 | BCLK 输出，3 MHz；无内部上下拉 |
| LR | M15 | 输出，0=左、1=右；同步选择接收声道；无内部上下拉 |
| WS | J16 | 输出，46.875 kHz；无内部上下拉 |
| G / V | GND / 3V3 | 电源 |

以上接线由用户再次更正确认：P11/R11 位于 PG256 Bank 2，M15 位于 Bank 1，
J16 位于 Bank 0，当前 VCCIO 均为 3.3 V，使用 LVCMOS33，没有已分配功能冲突。
WS2812 仍独立使用 T9。第二只麦克风接 DA=T6、CK=R8、LR=T8、WS=P9，
使用 Bank 3 的 LVCMOS33；相关历史问题见下文失败现场记录。

CPU/sys 60 MHz 以 clock-enable 分频生成 BCLK，WS 在 BCLK 下降沿变化，
输入经过两级 sys 同步后在 BCLK 上升沿采样。没有新增 PLL 或时钟域。
启动后需给模块至少 200 ms 稳定时间，停止后 BCLK/WS 回到低电平。

接收器持续产生时钟；`capture` 只采集下一组 512 个样本（10.923 ms），
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
（P11/R11），下方绘制青色右路（T6/R8），每路 512 点 / 10.923 ms，两个
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
.venv/Scripts/python.exe scripts/build.py --output-dir build/mic-new-pins --synthesize
.venv/Scripts/python.exe scripts/build.py --app firmware/examples/microphone_demo.c --output-dir build/mic-demo-diagnostic
.venv/Scripts/python.exe scripts/boot_upload.py --program --mode uart --output-dir build/mic-new-pins --image build/mic-demo-diagnostic/firmware/app.img
```

demo 串口命令：`status`、`test mic`、`dump`（512 个 PCM24 十六进制值）、
`left/right`、`hold/run`、`stop`、`reboot` 或 `!`。`hold` 冻结显示，麦克风时钟继续；
`stop` 关闭时钟并冻结画面，`run` 重新启动。

新增 CSR（含输入活动标志）改变镜像 ABI 为 `1467ae6a`，因此需要匹配的新 FPGA 配置与应用。
本次采用 SRAM 下载和 UART 执行，不写 Flash；软件复位后旧 Flash 应用会
因 ABI 不符被拒绝，需要重新 UART 装载。断电重启会恢复原有 Flash FPGA
配置和旧应用。构建时序/资源和实板验收分别以生成的 validation.json 与
验证记录为准，仿真或非恒定数据本身不代表实物声音响应已经通过。

`DA HIGH` / `DA_HIGH_SEEN` 表示从启动/clear 起，同步后的 DA 曾出现高电平。
它观察所有时隙，独立于 PCM 解码：若它为 0 且样本全零，应先检查模块供电、
接线和时钟到达；若它为 1 而 PCM 全零，再检查声道/格式。该标志不证明
声音有效，也不测量电压。

## 2026-10-01 最终新接线验收：通过

最终配置位于 `build/mic-new-pins-phase`，应用位于 `build/mic-demo-diagnostic`。
ABI 为 `1467ae6a`，FPGA bitstream SHA256：
`ec67e77d5c441e5d1e9de70833bae7d1fe04a02e4d57ac991083bcf0395b1ce2`。
最新 setup/hold 违例均为 0：Logic 15,726/20,736，Register 8,636/16,173，
CLS 9,098/10,368，BSRAM 35/46，PLL 4/4；麦克风新增一块 BSRAM，无新增 PLL。

新引脚布线曾出现 USB ready→输出使能的 -0.010 ns hold 违例，因此将独立
ULPI PLL 相位由 225° 改为 247.5°（增加一档 22.5°，60 MHz 下约 1.04 ns），
保留原来的外部 setup/hold 预算。CPU、DDR、USB 和显示频率没有变化。
最终配置的 37 条基础 monitor 命令调用全部通过，包括三次 USB-only restart
和共享 PHY reset；见该目录的 `firmware-verification.json`。

独立 demo 的同一颗麦克风左/右时隙选择各自通过 `test mic`，都有非恒定的
正负 PCM24、DA activity=1、512 样本完整读取。一次左时隙快照范围为
-5,486..+9,070，峰值 8,327。运行记录中的 FIFO overruns、demo errors、
USB errors/drops、LCD underflows 均为 0，见 `build/mic-new-pins-uart.log`。
用户确认说话/轻拍后绿色波形与 PEAK/P-P 数字明显变化，且 LCD 画面稳定。

另外实际加载基础 monitor 执行了新 `test mic` 命令，返回 PASS，读取 512 个
样本，min=0x00000a13、max=0x00004ad3、DA_HIGH_SEEN=1；记录位于
`build/mic-monitor-test.log`。之后恢复独立波形 demo，默认左时隙、自动增益。
本次只下载 FPGA SRAM 和 UART 应用，没有写 Flash，没有提交。

## 旧接线排查记录

旧接线的诊断 PnR（`build/mic-diagnostic-p3/validation.json`）：setup/hold 违例均为 0，
Logic 15,726/20,736，Register 8,636/16,173，CLS 9,092/10,368，BSRAM 35/46，
PLL 4/4。使用 Gowin timing_driven=1、place_option=3、route_option=1；
未放宽既有 USB 输出时序预算。仿真通过 signed24、左右声道、64 BCLK/frame、
两次快照、FIFO drain 和停止/取消，以及活动标志清零。

首次匹配 CSR 的配置 `build/mic-corrected` 完成 37 条 monitor 命令调用，
DDR/SD/LCD/USB/音频/网络/IO 的自动回归通过；记录在该目录的
`firmware-verification.json`。这不是最新诊断 bitstream 的重复全套验收。
该诊断版完成 SRAM 下载、ROM DDR 训练及 memtest、UART 应用装载；
运行中 USB errors/drops、LCD underflows、FIFO overruns 和 demo errors 均为 0。

但左右声道的 PCM 样本都为 0，`test mic` 实际为 FAIL。模块供电由用户测得约
3.3 V，说话/轻拍没有改变 PEAK/P-P。示波器观察提示 CK 一直高、DA 低或
偶有波形；FPGA 在每次重新启动后的短时检查中没有检测到 DA 高电平，曾在
较长运行中出现一次 sticky activity=1，不能据此声称有持续数据流。
stop 后用户测得 CK 仍约 3 V、其他信号为 0 V。旧 T7 的 PnR 报告显示内部
弱上拉 UP；核心板/Dock 原理图未找到该线的外部上拉，FPC 支路上的 22 Ω
是串联电阻。弱上拉通常不能压过正常驱动的低电平，因此没有把它定为根因。
用户随后换到上表新引脚，输出内部上下拉已显式禁用。
旧接线下缺少 CK 的具体原因未确定；不能把内部弱上拉直接定为根因。
换到新引脚后数据和实物波形验收通过，详见上一节。

## 2026-10-02 初次双麦克风测试（错误引脚假定）

用户在 T6/T7/T8/P9 接入另一只麦克风，保留 P11/R11/M15/J16 上的第一只。
双路仿真通过，覆盖独立正负 PCM24、同帧配对、两组 CK/WS 一致、LR=0/1、
FIFO drain、采样到一半停止并清空；记录 `build/mic-stereo-simulation.log`。

实际采用 `build/mic-stereo-p2`：ABI `e858c7bc`，bitstream SHA256
`1a8afaed7eba638aab9965744680c5dc899f8085b580ceb65a70a93d1d06e5ae`。
Gowin timing_driven=1、place_option=2、route_option=1，setup/hold 违例均为 0。
首轮 place_option=3 有 2 条 setup 违例（最差 -0.078 ns，SD DMA 地址到总线），
place_option=4 有 4 条（最差 -0.041 ns），均没有下载；对应报告保留在
`build/mic-stereo`、`build/mic-stereo-p4`。频率和时序约束没有放宽。

| 资源 | 使用 / 总量 |
| --- | --- |
| Logic / Register | 15,877/20,736；8,713/16,173 |
| CLS / BSRAM | 9,131/10,368；36/46 |
| I/O / IOLOGIC | 139/207；62/207 |
| rPLL / PRIMARY / LW | 4/4；8/8；8/8 |
| GCLK_PIN / CLKDIV / DHCEN / DLL / DQS | 7/8；1/8；1/16；1/4；2/9 |

新配置已 SRAM 下载，37 条基础 monitor 命令回归通过，见
`build/mic-stereo-p2/firmware-verification.json`。额外 `test mic` 通过，但
`test mic stereo` 连续两次失败：左路有变化的数据，右路 512 个样本全部为 0，
`DA_HIGH_SEEN=0`；见 `build/mic-stereo-monitor-tests.log`。
因此基础回归通过不能解释为双麦克风验收通过。

随后 UART 加载 `build/mic-stereo-demo/firmware/app.img`（31,888 bytes；
text=31,784、data=56、BSS=16,640），运行上下两条波形。demo 的双路测试也
失败，`dump` 显示左路变化、右路全部为 0；USB errors/drops、LCD underflows、
FIFO overruns、demo transport errors 均为 0。记录
`build/mic-stereo-demo-upload.log`、`build/mic-stereo-demo-tests.log`。
当时保持时钟运行，等待用户测量模块端 CK、接口 T7 焊盘、WS、LR 和供电。

RTL 明确 `microphone_second_bck = mic_bck`，与第一组 R11 共用内部 BCLK。
`build/mic-stereo-p2/gateware/impl/pnr/project.pin.html` 确认 T7/Bank3 为
LVCMOS33 输出、8 mA、PULL_MODE=NONE；T6/Bank3 为输入、弱下拉；T8/P9
为无内部上下拉的 LVCMOS33 输出。报告不能证明外部焊盘或模块端已收到时钟，
当时第二路无数据的根因尚未确定；随后确认接线分组有误，见下一节。
此配置没有写 Flash、没有提交。

## 实物排针分组更正

用户随后提供实物照片，确认实际连接在圈红的一列：从信号端到电源端为
`P6 / T7 / P8 / T9 / GND / 3V3`，不是此前假定的 `T6 / T7 / T8 / P9`。
照片旁边另一列为 `T6 / R8 / T8 / P9 / GND / 3V3`。此前四根信号的分配
混用了两列；接收器读 T6 而实际 DA 在 P6，LR/WS 输出也没有到达对应信号。
这是与全零样本和 DA activity=0 一致的直接接线证据；旧 CK 电压读数并不能
据此单独定位 T7 的驱动状态。

用户选择旁边的一列，避免麦克风 WS 与 T9 的 WS2812 共用。第二只已确认
重新接为 DA=T6、CK=R8、LR=T8、WS=P9，另接 GND/3V3；代码与首页接线
说明已同步，保留原来的 P11/R11/M15/J16 第一只。没有引入 T9 复用逻辑。
`build/mic-adjacent` 为此接线的重新构建输出，结果见下一节。

## 相邻列接线的重新验收

`build/mic-adjacent/validation.json`：ABI `e858c7bc`，bitstream SHA256
`fd7d2631ef1da55c54ea43129abaa865f903ad713117f4efc7c71447116cd266`；
timing_driven=1、place_option=2、route_option=1，setup/hold 违例均为 0。
这次只有引脚变化，没有改变 CSR，因此 ABI 与上次一致；ABI 不能用来区分
两个版本的接线，下载版本以 bitstream SHA256 和 CST 的 R8 约束为准。
资源 Logic 15,877/20,736，Register 8,713/16,173，CLS 9,131/10,368，
BSRAM 36/46，I/O 139/207，IOLOGIC 62/207，rPLL 4/4，PRIMARY/LW 8/8，
GCLK_PIN 6/8，CLKDIV 1/8，DHCEN 1/16，DLL 1/4，DQS 2/9。
CPU/sys 60 MHz、DDR 120 MHz、麦克风 BCLK 3 MHz / 每路 46,875 Hz 保持不变。

重新 SRAM 下载，37 条基础 monitor 命令回归通过，见
`build/mic-adjacent/firmware-verification.json`。`test mic` 通过，
`test mic stereo` 连续三次 PASS：每次 512 对变化的正负 PCM24，左右 DA
activity 均为 1，512 对数值全部不相同，见 `build/mic-adjacent-monitor-tests.log`。
旧引脚配置 FAIL 与实物分组更正后的 PASS，验证了原来的全零数据来自配置
与实际接线不一致；没有把引脚本身不可用或内部上拉定为原因。

随后加载 `build/mic-adjacent-demo/firmware/app.img`，31,888 bytes，
text=31,784、data=56、BSS=16,640。独立 demo 的双路检查和两次 stop/run
重新启动均通过，停止时 control/pairs/level/DA activity 清零，恢复后两路
重新读到变化数据。USB errors/drops、LCD underflows、FIFO overruns、demo
errors 均为 0，见 `build/mic-adjacent-demo-upload.log` 和
`build/mic-adjacent-demo-tests.log`。用户随后分别靠近两只说话，确认
两路对应声音响应正常，LCD 画面稳定。程序保持双路实时显示。

T9 仍独立连接 WS2812；没有实现 T9 复用。当前只下载 SRAM 和 UART 应用，
未写 Flash，未提交。输入依然是快照采样，不能将此验收解释为连续录音 DMA。

## 双路 demo 的实板绘图耗时（2026-10-02）

`microphone_stereo_demo.c` 的串口 `perf` 命令恢复实时运行，并累计 64 次成功
更新。使用 60 MHz sys 定时器记录采集等待、FIFO 读取、统计、CPU 绘图和
等待换帧各阶段的周期数；测量期间不输出 UART，最后统一打印平均/最小/最大
周期数。更新间隔由第一与第 64 次采集开始的时间差除以 63 得出，包含正常
USB/IRQ 服务和循环延时。它测量实际 demo 工作负载，不是纯 DDR 写带宽。

| 阶段 | 平均 ms | 最小 ms | 最大 ms |
| --- | ---: | ---: | ---: |
| 等待 512 对麦克风样本 | 11.382 | 10.988 | 16.876 |
| 从 CSR FIFO 读取 512 对 | 5.878 | 5.527 | 6.194 |
| 均值、峰值与增益统计 | 5.701 | 5.446 | 6.229 |
| 恢复旧曲线、绘制新曲线与状态文字 | 74.762 | 69.718 | 82.964 |
| 等待 LCD 帧边界切换缓冲区 | 6.751 | 0.345 | 16.522 |

正常更新不是重画完整的 480×272 屏幕：静态标题/网格保留，仅恢复旧曲线、
画两条新曲线并更新状态栏。绘图加换帧平均 81.513 ms，采集到换帧完成的
阶段耗时总和平均 104.474 ms；完成后额外等待约 33 ms，因此实测更新间隔
137.444 ms，即 7.276 FPS。LCD 以 9 MHz / 525 / 286 = 59.940 Hz 连续扫描，
其扫描刷新率与应用更新率不同。曲线跨度、文字和其他服务负载会影响绘图时间。

启动时完整背景初始化的单次记录：slot 1 为 530.352 ms、slot 0 为 231.164 ms。
这些包含绘制期间的正常 `hal_poll()` 服务，不含随后的换帧等待，不是 64 帧
平均，也不能作为通用整屏填充性能；首次 USB 枚举等服务可能影响启动负载。

固件构建 `build/mic-perf`：应用镜像 32,888 bytes，SHA256
`076e662feec2b86b71d1a941325cb4eb759c5d4d4941cad0acfc0a1a15931ce7`，
ABI `e858c7bc`。仅经 UART 装载应用，FPGA 仍是已验收的 `build/mic-adjacent`。
原始报告 `build/mic-perf-uart.log`，解析结果 `build/mic-perf-result.json`，
完整主机记录 `build/mic-perf-measurement.log`；下载与构建分别见
`build/mic-perf-upload.log` 和 `build/mic-perf-build.log`。测量后
`test mic stereo` PASS，demo errors、FIFO overruns、USB errors/drops、LCD
underflows 均为 0，见 `build/mic-perf-check.log`。保持实时显示，未写 Flash。
