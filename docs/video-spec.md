# 视频规格：480×272 并行 RGB LCD

日期：2026-09-30。用户决定暂时放弃 HDMI，先完成已连接的 4.3 英寸 RGB LCD。
设计只驱动 LCD，HDMI TMDS/时钟/引脚不实例化；原有 240×135 SPI LCD 与 SD 保留。

## 当前目标

480×272、RGB565、9 MHz 像素时钟、约 59.94006 Hz。
水平 active/front/sync/back=480/2/41/2，总 525；垂直=272/2/10/2，总 286；HS/VS 负极性。
DDR → SharedL2 → 内存控制器的 16 B read buffer → 16-bit RGB565 stream → 8 KiB 数据 FIFO → CDC → LCD 像素寄存器。宽度转换位于 [共享内存控制器](memory-controller.md)。
LCD DCLK 转发相位使数据/控制在外部时钟上升沿前稳定；面板实际显示仍需用户目视验收。

单帧 261,120 字节（255 KiB），stride 960，双帧有效像素 510 KiB。
帧地址由软件通过 `base0/base1` CSR 配置，要求 16 字节对齐，完整一帧位于
DDR `0x00001000..0x07ffffff` 范围内。BIOS 使用链接器分配的双缓冲，
[U-Boot framebuffer](uboot-framebuffer.md) 使用 VIDEO uclass 预留的单缓冲。
显示读取平均约 15.652 MB/s，active 消费 18 MB/s；CPU 60 Hz 全屏重绘加显示读取约 31.3344 MB/s，未含总线/DDR开销。

## 换帧和故障行为

扫描进入垂直消隐时请求一帧，DMA 每帧只生成一次有边界的读取，不提前循环预取下一帧。
只有上一帧末尾像素已消费/丢弃后才启动下一帧；在此处锁存 select，并更新 active。
软件先写后台帧并执行屏障，再改 select；active 确认后旧帧才能重新绘制。
选择槽位时锁存对应的 `base0/base1`；非法地址不启动 DMA，并置 `address_error`。
软件只在关闭显示且 DMA 已排空时修改两个 base。

消隐期不消耗正常帧像素。FIFO 缺像素时记一次 underflow，当前帧剩余部分输出黑色；
持续丢弃旧帧直到 last 标志，再在下一扫描边界重同步。时钟、DE、HS/VS 始终运行。
关闭显示时继续排空已发起的一帧，避免再次启用时读到旧数据。跨域 enable 在扫描帧边界才启用像素消费，避免半帧启动时产生冷启动欠载。
CSR 包含 enable、select、active、busy、frames、completed 和 underflows；暂不启用视频 IRQ。早期版本的 checksum/test 测试 CSR 与 fbpattern/fbmemory FPGA 测试图开关已移除，像素数据只来自 DDR 帧缓冲。

## 引脚和本地依据

参考 ../examples/RGB_lcd/480x272_4.3inch_lcd 的 top.v、vga_timing.v、lcd.cst 和 video_pll.v（路径相对于 riscv-mini 根目录）。
均为 FPGA 球位，LVCMOS33；颜色位按 LSB → MSB：

| 信号 | 引脚 |
| --- | --- |
| DCLK / HSYNC / VSYNC / DE | R9 / A15 / D14 / E15 |
| R[0:4] | L9 / N8 / N9 / N7 / N6 |
| G[0:5] | D11 / A11 / B11 / P7 / R7 / D10 |
| B[0:4] | B12 / C12 / B13 / A14 / B14 |

当前 LiteX platform 的 lcd.vsync 指向 SD detect 使用的 D15，本设计显式定义 RGB LCD 接口并改用示例 D14。
背光使用 E10 / RGB_LCD_BL，高电平开启，独立于视频 enable。
Dock-3713 Rev1.1 原理图 P006_LCD（PDF 第 4 页，图纸编号 6/9）显示它直接接 U5 LP3320
的 EN，并由 R33 10K 上拉；5inch 参考工程的 rgb_lcd.cst 同样将 lcd_bl 约束到 E10。
CSR 复位为 100，保留原有上电常亮行为；触摸和额外 reset 仍不驱动。
扫描时序按示例参数重新实现，未复制其未复位计数器或 VS_POL 引用 HS_POL 的写法。

## CSR 窗口 skip 与背光

在现有寄存器末尾追加 `skip`（1 bit，复位 0）和 `backlight`（7 bit，复位 100），
保留旧寄存器偏移。`backlight=0..100` 设定 RGB LCD 背光 PWM 占空比百分数，0 为常低关闭，100 为常高全亮，
有效 7 bit 值 101..127 输出饱和为全亮（CSR 读回保留原始 7 bit 写值）。PWM 固定 45kHz、1% 步进，
占空比在下一个周期边界生效，延迟约 23us（包括占空比同步）；输出经过寄存器，避免计数器进位毛刺。
PWM 复用 9MHz video 时钟，每个占空比步长为 2 个像素时钟；配置同步到 video，输出电平同步回 sys，不增加时钟域；不停止像素时钟、DMA 或计数器。

`skip=0` 保持完整 480×272 帧的原有行为，包括 RGB565 ring/YWRAP 和 INDEX8 的输出窗口遮黑。
`skip=1` 时，扫描窗口之外直接输出 RGB565 黑色零值，不消费 FIFO 数据、不触发缺像素错误；
窗口内按行连续消费紧凑帧。支持 RGB565 和 INDEX8，也可与有效 DMA ring 组合。
面板扫描时序仍为 525×286，DE 仍覆盖完整 480×272，刷新率不变。

窗口复用 `window_start/end`：h 位于 [0:10]，v 位于 [10:19]，start 的 bit19 必须为 1；
end 为独占边界。用户像素坐标转换为扫描坐标时，x 加 45、y 加 14。
合法范围为 `45 <= h0 < h1 <= 525`、`14 <= v0 < v1 <= 286`。
DMA 字节数由 `(h1-h0)*(v1-v0)*(format ? 1 : 2)` 自动计算，必须为非零的 16 字节倍数。
无 ring 时根据紧凑帧长度检查完整 DDR 区间；非法窗口、对齐或地址配置置 address_error，不启动 DMA。
窗口、format、skip、base/ring 都必须在 enable=0 且 busy=0 后修改，配置完等待至少
24 个 sys 时钟供串行面积计算和 CDC 稳定，再开启扫描。驱动使用 1ms 等待。
切换回 BIOS 普通图形/TTY 时，驱动初始化与 format 配置显式清除 skip。

居中显示紧凑 320×200 INDEX8 帧的配置示例（先关 enable 并等 busy=0）：

```c
rgb_lcd_format_write(1);
rgb_lcd_window_start_write(125u | (50u << 10) | (1u << 19));
rgb_lcd_window_end_write(445u | (250u << 10));
rgb_lcd_skip_write(1);
/* base0/base1 指向各 64000 字节、16 字节对齐的 DDR 缓冲；ring 默认关闭。 */
rgb_lcd_base0_write(front);
rgb_lcd_base1_write(back);
rgb_lcd_wrap_end_write(0);
/* 写好 palette，fence rw,rw，并等待 1ms 后开启 enable。 */
```

此配置每帧读取 4000 次 16B burst，而全屏 INDEX8 为 8160 次；黑边不占 VRAM。
它提供应用免复制的扫描条件，应用仍需管理前后台缓冲所有权，不能覆盖正在扫描的帧。

针对性 RTL 回归：`tests/primer20k_vexriscv/lcd_skip_verilog_test.py --iverilog <iverilog.exe> --case <case>`，
case 包括 doom、small、full、rgb、range、stall、starve；检查完整像素流、黑边、DMA 请求、
换帧/调色板原子切换、背光输出、地址/窗口拒绝、延迟与欠载后的恢复。

板端 `test lcd skip` 检查 RGB565/INDEX8 各两种紧凑尺寸、帧完成与欠载计数、
背光 CSR 设为 0/25/50/75/100 时扫描继续运行，并恢复普通全屏模式；物理明暗仍需目视确认。

`backlight_level`（只读、+0x54）返回 PWM 输出寄存器当前电平，可用于数字波形采样。
板端 `test lcd skip` 对 0/25/50/75/100 的输出采样测量频率与占空比；模拟占空比精确性
由 RTL 逐时钟验证，板端采样允许测量误差，不能代替引脚示波器或屏幕亮度校准。
