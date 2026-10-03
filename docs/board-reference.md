# 板级参考

Tang Primer 20K 核心板 + Dock 3713 的器件、引脚与电气事实。构建脚本里的机器可读版本是 `gateware/board.json`；本页记录 board.json 表达不了的约束与出处。

## 器件与容量

- FPGA：GW2A-LV18PG256C8/I7（GW2A-18），20,736 LUT4、15,552 FF、46 个 BSRAM 共 828 Kbit（约 103.5 KiB）、4 个 PLL。
- 配置 Flash：原理图标注 SPI NOR 32 Mbit（4 MiB）；本机实测 JEDEC `0x0b4017`，XTX 8 MiB。构建与分区以实测容量为准。
- DDR：H5TQ1G63EFR-PBC，64M×16 = 128 MiB，8 bank、13 位行地址 A0–A12、10 位列地址 A0–A9，VDD/VDDQ 1.5 V。profile：`nbanks=8 nrows=8192 ncols=1024 data_width=16` 单 rank；`PB` = DDR3-1600 11-11-11 速度档，`C` = 商业温度。
- 注意：原理图上的 IMD128M16R39CG8GNF-125（256 MiB，8×16384×1024）是另一种装配，不是本板的颗粒；两者容量与行地址位数都不同。

## 引脚表

| 功能 | 引脚 |
| --- | --- |
| 系统时钟 | H11（27 MHz 输入） |
| 系统复位 | T10，低有效（Key1 复用） |
| UART | RX M11、TX T13，115200 8N1 |
| SD | CLK N10、CMD R14、DAT0–3 M8/M7/M10/N11、卡检测 D15（低=插入） |
| SPI LCD | SCK F12、MOSI L15、CS C16、DC J13、reset G13、背光 P12（低有效） |
| RGB LCD | 像素时钟 R9、HSYNC A15、VSYNC D14、DE E15；R 低位起 L9 N8 N9 N7 N6，G 低位起 D11 A11 B11 P7 R7 D10，B 低位起 B12 C12 B13 A14 B14 |
| Ethernet | REF_CLK A9（PHY 50 MHz）、RXD F15/C9、RX_DV(CRS_DV) M6、RX_ER L8、TXD D16/E14、TX_EN E16、MDC F14、MDIO F16 |
| USB ULPI | CLKOUT T15、DATA G11 H12 J12 H13 T14 R13 P13 R12、STP K11、DIR K12、NXT K13 |
| 音频 PT8211 | BCK N15、DIN P15、WS P16、PA_EN R16 |
| 麦克风 1 | DA P11、BCK R11、LR M15、WS J16 |
| 麦克风 2 | DA T6、BCK R8、LR T8、WS P9 |
| SPI Flash | CS M9、CLK L10、MOSI R10、MISO P10 |
| LED | C13 A13 N16 N14 L14 L16，低有效 |
| 按键 | Key1 = T10（复位复用）、Key2 T3、Key3 T2、Key4 D7、Key5 C7，低有效，5 ms 去抖 |
| DIP 开关 | E9 E8 T4 T5（ON = 高） |
| WS2812B | T9 |
| 共用 PHY reset | F10（Ethernet 与 USB PHY 共用） |
| 系统/配置 | 配置开关 B10 |
| HDMI（暂缓，未实例化） | CK G16/H15、D0 H14/H16、D1 J15/K16、D2 K14/K15 |

## 电气与时序约束

- GPIO 电压：Key1 上拉到 3.3 V，其余四个按键与 DIP 开关上拉到 1.5 V。输入约束必须按真实 bank 电压写，不能整批 LVCMOS33（`board_io.input_io_standard=LVCMOS15`，输出 LVCMOS33）。
- C13 / A13 是 DONE / READY 复用配置引脚，只能在配置完成后作为 LED 使用。
- Flash 的 WP/IO2 与 HOLD/IO3 由电阻拉高、未接 FPGA，因此只能做单线 SPI；芯片支持 Quad 不等于板级支持 QSPI。
- F10 必须由始终运行的 sys 域复位驱动；复位生成逻辑不能依赖 USB CLKOUT，否则 USB PHY 初始化期间会失去复位。
- USB3317 自带 26 MHz 晶振，ULPI CLKOUT 输出 60 MHz；VBUS 硬连到 5 V、CPEN 未接，HAL 不得提供不存在的 VBUS 断电能力；模式开关必须置于 Host。PHY 固定 FS，LS 未验证，Hub / MSC / USB Audio / 480 Mbps 均不作承诺（PHY 支持 HS 不等于 OHCI 支持 HS）。
- Ethernet MDC 用 ≤500 kHz bitbang 产生，不额外占用 PLL。F14 / F16 同时接到 HDMI DDC 与触摸屏/摄像头 I2C，做 SCL/SDA 复用前需逐接口确认。用户确认（2026-10-01）：R58/R59 未贴（PHY 管理总线的可选上拉），信号仍经过 RN7，不能声称 PHY 已被隔离。
- SD：microSD 位于核心板 J2，四条数据线全部到 FPGA。限制：30 MHz 超过 standard-speed 默认上限；20 MHz 的奇数分频不能声称精确。SPI 与 native SD 共用同一组引脚，一次构建只能有一个控制器。
- 音频：PT8211-S 为 16 位立体声 DAC → LPA4809 → 耳机，板上无 ADC，不能录音。数据手册规定 WS 低 = 右声道、高 = 左声道（本地示例注释相反，以手册为准）。
- 时钟：USB PHY 的 60 MHz 与 CPU 的 60 MHz 是异步关系，频率相同不消除 CDC。只有 USB ULPI 初始化的相移 60 MHz PLL 与 OHCI 的 48 MHz 需要新 PLL，其余都从 sys/DDR 域分频。
- 复用冲突：LiteX 平台把 `lcd.vsync` 接到 `CARD1:103`（D15），与 SD 卡检测冲突，本设计使用 D14。
- 参考工程 `Cam2HDMI` 的 CST 把部分 bank 设为 2.5 V，与 LiteX 的 3.3 V 不同，CST 不能直接合并。

## 出处

- Dock-3713 原理图 Rev 1.1（2026-09-12）第 1、5、6、7、9 页；核心板 3690 原理图 Rev 0.0.2 第 1 页。
- 本地参考示例：`../examples/USB/usb_serial`、`../examples/Ethernet/verilog_UDP/udp_18k`、`../examples/PT8211`、`../examples/WS2812`（示例参数需按 60 MHz 重新换算）。
- 上游：litex-boards 的 TangPrimer 20K target/platform、litex `video.py`、litedram `modules.py`。
- 参考 caution：`kami4ka/tang_computer` 的 serial-mode USB Host 参考实现接线与本地原理图不同，以本地原理图为准，不要照抄其 CDC/复位方案。
