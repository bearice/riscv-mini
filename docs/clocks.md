# 当前时钟树

固定 CPU/sys 60 MHz、DDR CK 120 MHz；USB M9 新增时钟不改变现有设备频率。
最终资源和上板状态以相应验证页为准。

```text
核心板 27 MHz
  ├─ POR / DDR init 域：27 MHz
  ├─ 主 rPLL：120 MHz
  │    └─ DHCEN（DDR 初始化期间可暂停）：120 MHz
  │         ├─ DDR PHY / CK：120 MHz（240 MT/s，x16）
  │         └─ CLKDIV /2：CPU、Wishbone、CSR、DDR 控制逻辑 60 MHz
  ├─ LCD rPLL：RGB LCD 像素/扫描 9 MHz
  └─ USB rPLL：OHCI serial PHY 引擎 48 MHz（FS 12 Mbit/s）
Dock RTL8201F 25 MHz 晶振
  └─ PHY REF_CLK / FPGA RMII RX、TX、参考计数：50 MHz
Dock USB3317 26 MHz 晶振
  └─ PHY ULPI CLKOUT：60 MHz（独立于 sys 60 MHz）
       └─ ULPI rPLL：60 MHz / 247.5°（只用于 PHY 初始化与状态）
```

USB PLL 数量为两颗，加上主时钟和显示 PLL，共四颗。OHCI 的控制寄存器、
DMA、IRQ 在 sys 域，USB 收发引擎在 48 MHz 域，通过 core 内的同步器、toggle
握手跨域。串行 DP/DM 接收先同步到 48 MHz；PHY 状态同步到 sys。ULPI 初始化
采样采用 60 MHz 的原生下降沿触发器，数据和 STP 输出为寄存器。
247.5° 相位与 FPGA 内部时钟布线延迟共同决定实际采样/发射位置；不能单看
PLL 相位推断接口边沿。ULPI 输出约束以 PHY 外部时钟为参考保留 setup 和
hold 检查，初始化输出仅增加一个 setup 周期，没有用对应 hold 例外放宽。

| 设备 | 信号频率 / 更新率 | 来源 |
| --- | --- | --- |
| UART | 115200 bit/s，8N1 | sys 分频 |
| Flash SPI | 10 MHz | sys 分频 |
| SPI LCD | 6 MHz | sys 分频 |
| 原生 SD | 初始化 400 kHz，工作 7.5 MHz，四位 SDR | sys /150、/8 |
| RGB LCD | 9 MHz 像素，480×272，约 59.94 Hz | 显示 PLL；总时序 525×286 |
| PT8211 | BCK 1.5 MHz；46,875 stereo frame/s | sys /40，每帧 32 bit |
| I2S 麦克风 | BCLK 3 MHz；46,875 samples/s；WS 46.875 kHz | sys /20，每帧 64 bit；无新增 PLL/时钟域 |
| MDIO | MDC ≤500 kHz，实际包含软件开销 | GPIO bitbang；每半周期等待至少 1 µs |
| WS2812B | 800 kbit/s；latch ≥300 µs | sys 状态机 |
| timer1 | 1 kHz IRQ | 60,000 sys 周期 |
| OHCI | PHY 48 MHz；FS 12 Mbit/s；1 ms USB frame | USB PLL、PHY /4 tick |

复位异步断言、同步释放。F10 共同复位 Ethernet 和 USB PHY，HAL 先停止
两控制器再复位并重建。USB-only 重启先停止 OHCI DMA，再用 STP 退出串行
模式并重新初始化，保持 F10 释放。ULPI PLL lock 参与初始化域复位；USB
48 MHz 域另有 lock 和 OHCI control reset。USB PHY clock 与 sys 名义频率相同
不代表同步，二者之间仍需 CDC。

M10 修复连续停止后 ready/ID 残留：USB-only disable 保持 ULPI PLL 运行，
以 enable 复位初始化 FSM，确保同步寄存器有时钟完成清零。F10 物理复位仍
复位该 PLL。当前 rPLL、PRIMARY、LW 分别为 4/4、8/8、8/8，完整占用见
[M10 资源报告](m10-review.md)。
