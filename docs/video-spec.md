# M3 视频规格：480×272 并行 RGB LCD

日期：2026-09-30。用户决定暂时放弃 HDMI，先完成已连接的 4.3 英寸 RGB LCD。
当前里程碑只驱动 LCD，HDMI TMDS/时钟/引脚不实例化；原有 240×135 SPI LCD 与 SD 保留。

## 当前目标

480×272、RGB565、9 MHz 像素时钟、约 59.94006 Hz。
水平 active/front/sync/back=480/2/41/2，总 525；垂直=272/2/10/2，总 286；HS/VS 负极性。
DDR → 单个 LiteDRAM native 128-bit DMA → 8 KiB 数据 FIFO → RGB565 转换/CDC → LCD 像素寄存器。
LCD DCLK 转发相位使数据/控制在外部时钟上升沿前稳定；面板实际显示仍需用户目视验收。

单帧 261,120 字节（255 KiB），stride 960，双帧有效像素 510 KiB。
帧地址为 0x47E00000 与 0x47E40000，间距 256 KiB；保留区沿用 DDR 尾部 2 MiB。
显示读取平均约 15.652 MB/s，active 消费 18 MB/s；CPU 60 Hz 全屏重绘加显示读取约 31.3344 MB/s，未含总线/DDR开销。

## 换帧和故障行为

扫描进入垂直消隐时请求一帧，DMA 每帧只生成一次有边界的读取，不提前循环预取下一帧。
只有上一帧末尾像素已消费/丢弃后才启动下一帧；在此处锁存 select，并更新 active。
软件先写后台帧并执行屏障，再改 select；active 确认后旧帧才能重新绘制。
帧地址仅能选择固定两槽，无法用 CSR 读取任意 DDR 区域。

消隐期不消耗正常帧像素。FIFO 缺像素时记一次 underflow，当前帧剩余部分输出黑色；
持续丢弃旧帧直到 last 标志，再在下一扫描边界重同步。时钟、DE、HS/VS 始终运行。
关闭显示时继续排空已发起的一帧，避免再次启用时读到旧数据。跨域 enable 在扫描帧边界才启用像素消费，避免半帧启动时产生冷启动欠载。
CSR 包含 enable、select、active、busy、frames、completed、checksum、test 和 underflows；暂不启用视频 IRQ。checksum 为每帧 RGB565 像素的 32-bit 模和，在 native DMA 输出处计算；用于检测测试图数据变化，不是 CRC，也不能检测像素重排。
fbpattern 切换到 FPGA 直接色带，fbmemory 恢复 DDR 帧缓冲；两者沿用同一 LCD 输出时序。

## 引脚和本地依据

参考 ../examples/RGB_lcd/480x272_4.3inch_lcd 的 top.v、vga_timing.v、lcd.cst 和 video_pll.v（路径相对于 riscv-mini 根目录）。
均为 FPGA 球位，LVCMOS33；颜色位按 LSB → MSB：

| 信号 | 引脚 |
| --- | --- |
| DCLK / HSYNC / VSYNC / DE | R9 / A15 / D14 / E15 |
| R[0:4] | L9 / N8 / N9 / N7 / N6 |
| G[0:5] | D11 / A11 / B11 / P7 / R7 / D10 |
| B[0:4] | B12 / C12 / B13 / A14 / B14 |

当前 LiteX platform 的 lcd.vsync 指向 SD detect 使用的 D15，M3 显式定义 RGB LCD 接口并改用示例 D14。
触摸、额外 reset 和可控背光不属于本地示例的扫描端口，当前不驱动这些额外信号。
扫描时序按示例参数重新实现，未复制其未复位计数器或 VS_POL 引用 HS_POL 的写法。

## 验收与后续

先通过完整扫描帧的像素顺序、同步宽度及缺数恢复仿真，再综合/布局布线、SRAM 下载。
上板验证 DDR、SPI/SD 回归、完整帧 completed 计数增长、双缓冲切换、underflow；用户确认 RGB 色带、白色边框及首末像素显示。
M4 在 LCD 运行下同时进行内存复制、SD CRC 读取和 SPI LCD 更新，持续至少 5 分钟（2026-10-01 按用户要求调整，自动测试实际 390.344 秒 / 246 轮通过）。
HDMI 和 upscale 延后，未来若重新启用，应单独确定标准输出时序、缩放、DMA/缓存和跨域换帧策略。

总体规划见 [system-plan.md](system-plan.md)，机器目标见 [board.json](../gateware/board.json)。

M4 当前采用 CPU/sys 60 MHz、DDR CK 120 MHz DLL-off CL6/CWL6，单物理 native 事务调度；LCD 像素时钟、同步参数、帧地址和色彩格式保持上述规格。最终并发结果见 [m4-validation.md](m4-validation.md)。
