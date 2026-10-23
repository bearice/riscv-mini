# AE350 P6 视频验收

日期：2026-10-23。P6a、P6b、P6c 和 P6d 数字视频/并发验收已通过。

## 硬件与时序

HDMI TX 使用 Dock 的 TMDS0。模式为 640×480 RGB565，像素时钟
25 MHz，800×525 总时序，对应源帧率 59.5238 Hz。系统、核心、数据、
DDR CK 为 80/800/100/400 MHz。

完整 PnR 候选 `ae04a88908624cdc766d882463d095cefb384c433baa12e72267b5f55621828b`
的 setup/hold 违例为 0/0，最小脉宽裕量 +0.037 ns。
资源为 Logic 13873、FF 17627、BSRAM 77、DSP 5。
构建记录：`build/runs/20261023T020354847191Z-build-ae350-p6b-decode-pipeline-7691a3/result.json`。
AHB 传输锁存与解码分拍，DDR 仲裁保持在途响应所有权，视频在垂直消隐应用配置。

## 已完成实板检查

| 检查 | 结果 | 记录 |
| --- | --- | --- |
| 无 DDR 测试图，MJPEG/60，30 分钟 | 108016 张检查帧，图样错误 0 | `20261022T171446650410Z-board-ae350-video-suite` |
| RGB565 双缓冲，YUYV/30 | 100 次翻页、100 次停启、3 项非法参数拒绝；guard 通过；757 张检查帧、图样错误 0 | `20261023T021137875483Z-board-ae350-video-suite` |
| BIOS VT/共享字体 TTY，YUYV/30 | 渲染掩码及前景背景检查通过；306 张检查帧、图样错误 0 | `20261023T021310429739Z-board-ae350-video-suite` |
| audio/mic/CPU/video 并发预检 | 70 秒流运行；5400 张 HDMI 检查帧、图样错误 0；数字音频与视频计数检查通过 | `20261023T021412116546Z-board-ae350-audio-suite` |
| audio/mic/CPU/video 并发长测 | 1810 秒流运行；109816 张 HDMI 检查帧、图样错误 0；音视频错误与 guard 错误为 0 | `20261023T021641627444Z-board-ae350-audio-suite` |

RGB565 诊断记录 52 轮 CPU 压力，视频欠载、缺失像素、地址错误、超时、
响应错误与描述符溢出均为 0，最大 DDR 服务等待 104 个 100 MHz UI 周期。
RGB565 采集窗口的 USB 时间戳无倒退，估算丢帧为 0，最大间隔 36.136 ms。
预检中 FPGA raster 计数在 70.0037 秒 UART 窗口内增加 4167 帧，
观测为 59.5255 Hz，与配置的 59.5238 Hz 一致；该测量含串口报告开销。

长测同时播放/采集约 8688 万个音频 sample frames，audio underrun、mic
overrun、符号检查与 guard 错误均为 0。CPU 压力计数 11970854。
视频硬件记录 107963 个 raster frames、2069208210 次32B读命令，视频欠载、
缺失像素、地址错误、超时、响应错误与描述符溢出均为 0。
最大 DDR 服务等待 105 个 UI 周期（1.05 μs），保守 FIFO 最低水位为1。
1810.0031 秒 UART 窗口内增加 107739 个 raster frames，实测 59.5242 Hz。
视频采集子记录为 `20261023T021717324604Z-board-ae350-audio-video-capture`；
109806 个有效 USB 时间戳无倒退、估算丢帧为0，最大间隔 20.406 ms。

源实现的相关检查包括 burst 38 项、CDC 4 项、基本 DDR 读写与从设备切换、
构建入口 23 项，以及视频 reader/arbiter/pixel/scanout、HAL/VT、PHY 复位和
PLL 模型检查。验收使用提交前工作树构建的明确产物，身份见上述记录。

## 采集与运行边界

所有上板均为 SRAM 临时装载，固件通过 UART 装入 DDR。未写 Flash，未更新
`current`。板测期间音视频并发完成前不暂停 CPU。
长测后再次通过 TTY 采集（`20261023T024828993810Z-board-ae350-video-suite`），
保留 TTY 在 FPGA SRAM 运行；两路 JTAG 的原始 FTDI 驱动绑定及采集卡 ACL 已恢复。

UVC 输出帧率不能独立证明 HDMI 输入帧率。逐帧颜色及文字检查针对送达的
USB 帧；整数 PTS 的间隔检查估算 USB 丢帧，静态画面不能识别重复 HDMI 帧。
长测使用 MJPEG，短测使用 YUYV；本报告不声明 HDMI 音频或模拟音频电气验收。

构建读取本仓库 Board/Machine、源代码与本地 IP。厂商 demo 的来源说明保留
在 PROVENANCE；构建无 `--reference-root`，不会读取外部参考工程。
