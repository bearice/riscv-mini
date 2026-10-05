# 当前时钟树

固定 CPU/sys 60 MHz、DDR CK 120 MHz；默认 USB 为 sys60 PIO/串行后端。
资源总量见 [系统设计](system-design.md)。

```text
核心板 27 MHz
  ├─ POR / DDR init 域：27 MHz
  ├─ 主 rPLL：120 MHz
  │    └─ DHCEN（DDR 初始化期间可暂停）：120 MHz
  │         ├─ DDR PHY / CK：120 MHz（240 MT/s，x16）
  │         └─ CLKDIV /2：CPU、Wishbone、CSR、DDR 控制逻辑 60 MHz
  ├─ LCD rPLL：RGB LCD 像素/扫描 9 MHz
  └─ sys 60 MHz 同时驱动 USB PIO Host / serial PHY（FS 12 Mbit/s）
Dock RTL8201F 25 MHz 晶振
  └─ PHY REF_CLK / FPGA RMII RX、TX、参考计数：50 MHz
Dock USB3317 26 MHz 晶振
  └─ PHY ULPI CLKOUT：60 MHz（独立于 sys 60 MHz）
       └─ ULPI rPLL：60 MHz / 225°（默认 Ultra，只用于 PHY 初始化与状态）
```

默认 USB 只有初始化用的一颗 PLL，加上主时钟和显示 PLL，共三颗。
PIO Host、串行收发器、寄存桥接和 IRQ 均在 sys 60 MHz 域，无 USB DMA，
每个全速位使用五个 sys 周期。串行 DP/DM/RCV 的第一级输入采样异步，
后续接收流水线保持正常时序约束；ULPI PHY 状态同步到 sys。ULPI 初始化
采样采用 60 MHz 的原生下降沿触发器，数据和 STP 输出为寄存器。
默认 Ultra 使用 225°，可选 OHCI 保留 247.5°。相位与 FPGA 内部时钟布线延迟共同决定实际采样/发射位置；不能单看
PLL 相位推断接口边沿。ULPI 输出约束以 PHY 外部时钟为参考保留 setup 和
hold 检查，初始化输出仅增加一个 setup 周期，没有用对应 hold 例外放宽。

| 设备 | 信号频率 / 更新率 | 来源 |
| --- | --- | --- |
| OpenSBI machine timer | 60 MHz、64 位计数 / comparator，MTIP | sys；仅 MMU 配置启用，无新增 PLL |
| UART | 115200 bit/s，8N1 | sys 分频 |
| Flash SPI | 10 MHz | sys 分频 |
| SPI LCD | 6 MHz | sys 分频 |
| 原生 SD | 初始化 400 kHz，读 15 MHz / 写 7.5 MHz，四位 SDR | sys /150、/4（读）、/8（写） |
| RGB LCD | 9 MHz 像素，480×272，约 59.94 Hz | 显示 PLL；总时序 525×286 |
| PT8211 | 平均 BCK 1.536 MHz；48,000 stereo frame/s | 共用 DDS /2 边沿使能，每帧 32 bit |
| I2S 麦克风 | 平均 BCLK 3.072 MHz；48,000 samples/s；WS 48 kHz | 共用 DDS 边沿使能，每帧 64 bit；无新增 PLL/时钟域 |
| MDIO | MDC ≤500 kHz，实际包含软件开销 | GPIO bitbang；每半周期等待至少 1 µs |
| WS2812B | 800 kbit/s；latch ≥300 µs | sys 状态机 |
| timer1 | 1 kHz IRQ | 60,000 sys 周期 |
| USB PIO | Host/PHY 60 MHz；FS 12 Mbit/s；1 ms USB frame | sys、PHY /5 tick |

复位异步断言、同步释放。F10 共同复位 Ethernet 和 USB PHY，HAL 先停止
两控制器再复位并重建。USB-only 重启先停止轮询并完成 TinyUSB 清理，再
复位 Host 和寄存桥接，用 STP 退出串行
模式并重新初始化，保持 F10 释放。ULPI PLL lock 参与初始化域复位；USB
Host 和桥接复位来自 sys reset、USB reset CSR 和同步后的 ready。
USB3317 CLKOUT 与 sys 名义频率相同但独立，初始化状态仍需 CDC；
新的 Host 和串行引擎直接使用 sys，与该外部 60 MHz 不构成同步关系。

USB 停止/重启：USB-only disable 保持 ULPI PLL 运行，
以 enable 复位初始化 FSM，确保同步寄存器有时钟完成清零。F10 物理复位仍
复位该 PLL。当前 rPLL、PRIMARY、LW 分别为 3/4、8/8、8/8，完整占用见
[轻量 USB 资源报告](usb-light.md)。

使用 `--usb-backend ohci` 可回到OHCI 时钟配置：额外的 48 MHz PLL
驱动 Spinal OHCI serial PHY，控制寄存器、DMA 和 IRQ 在 sys 域，通过核内
同步器和 toggle 握手跨域。该配置总计四颗 PLL，OHCI 后端说明见
[USB Host](usb.md)。

## 音频 DDS

默认 `--audio-clock dds`。相位累加器按构建时 `sys_clk_freq` 与 `128 × 48000` 求最大公约数，产生精确有理数速率的麦克风 BCK 边沿使能；DAC 每隔一次使用同一使能。60 MHz 下 step/modulus 为 64/625，麦克风相邻边沿间隔 9 或 10 sys 周期，DAC 为 19 或 20；完整帧为 1250 sys 周期。120 MHz 参数仿真时比值为 32/625，仍为 48 kHz。

GPIO 边沿带有小于一个 sys 周期的量化误差；60 MHz 下一个周期为 16.667 ns。DDS 不提供独立低抖动的 12.288/24.576 MHz MCLK；PT8211 与当前 I2S 麦克风只需 BCK/WS。所有 PCM FIFO、DMA 和串行状态机保留在 sys 域，没有音频 CDC 或新增 PLL。

`--audio-clock sys` 是 DDS 的兼容别名；`--audio-clock legacy` 提供整数分频 46,875 Hz。当前默认 DDS 不占用独立音频 PLL。
