# M9 USB Host 验证记录

日期：2026-10-01。前置 M8 提交 `27d877235039c251b98e494f11d0aa6fd356d78b`。
M9 接入 USB3317、Spinal OHCI、TinyUSB Host、DDR 描述符/DMA、IRQ 和 HID HAL。
用户不在设备旁，明确将键盘实物按键测试留到之后；本轮没有要求拔出接收器。

## 构建、时钟与资源

| 项目 | 最终硬件构建 |
| --- | --- |
| CPU / sys / DDR CK | VexRiscv lite RV32IM，60 / 60 / 120 MHz，DLL-off CL6/CWL6 |
| USB 收发 / ULPI 初始化 | OHCI 48 MHz / 相移 225° 的 60 MHz ULPI PLL；FS 12 Mbit/s |
| ROM / SRAM / boot.bin | 8,192 / 8,192 / **6,568 字节**；ROM 未链接 USB、SD、显示或音频 HAL |
| 基础 app.img / BSS | 46,476 / 11,112 字节，应用及 USB 数据结构均在 DDR |
| ABI | 原生 SD `945c4952` |
| Logic / Register | 15,528 / 20,736；8,414 / 16,173 |
| BSRAM / rPLL | **34 / 46；4 / 4** |
| Gowin PnR | setup 0、hold 0 违反端点 |

FPGA bitstream SHA256：
`367178ee2624636824e32d40f909c43010c17feec01d4c39181c6a615bececf5`。

基础 app.img SHA256：
`1da2bc0ea2f9d0fe3569c4c9d5853b0dc308b77409c206b24a35e6bcf91c5f53`。

最终独立并发示例 app.img 为 55,416 字节、BSS 150,376 字节（含 128 KiB
PCM ring），SHA256：
`7543e2cddd65932e996e30d53011200da6e4cc5c15a8dd922a36b10c74168c8f`。

与 M8 比较增加 2,914 Logic、1,708 Register、1 BSRAM 和 2 PLL。CPU/DDR
未提频，现有 SD、显示、Flash、RMII、音频频率见 [时钟树](clocks.md)。
当前没有空闲 PLL。资源占用来自实际 Gowin 推断，不能按描述符字节数估算。

DDR 32-bit Wishbone 与 OHCI 控制总线增加单事务寄存接口以限制组合路径。
原生 SD 的读请求增加两项 descriptor FIFO，保持 block_length/last 直到 PHY
接收，并在 abort 时清空；valid/ready 同时成立才增加发出块数。
异步时序仅豁免首级同步器及有协议保证的跨域载荷 hold 路径；第二级、
CPU/DDR 正常路径及 ULPI 输出 hold 仍检查。ULPI setup 周期标签与物理
PLL/时钟插入延迟的关系见 [USB](usb.md)，不能以宽泛 false-path 宣称时序通过。

## 枚举、DMA 与复位

- 实板 USB3317 ULPI ID 为 `00060424`，对应 Vendor/Product `0424:0006`。
- 已连接接收器为 Logitech **`046D:C52B`**，全速，枚举出 **3 个 HID 接口**。
- 枚举探针 HCCA 在 DDR `0x40831900`，后续示例为 `0x40831A00`，均满足
  256 字节对齐；OHCI frame 持续推进。
  地址取决于应用链接布局，不是 API 固定地址。
- 三次 USB-only 软件重启均重新枚举；通过 STP 退出 serial 模式重新配置 PHY，
  保持 F10 释放。共享 F10 复位后 USB 与 Ethernet 均恢复，UID 派生 MAC 不变。
- USB PHY/controller 错误、raw report queue drop、key queue drop 为 0。
  空闲接收器枚举和 OHCI 进度不能替代真实按键输入验收。
- 直接连接现有 Ethernet 7 / ifIndex79，IPv4 `169.254.48.203`；MAC
  `8E:6F:09:E9:70:28`，示例 IP `169.254.20.20` / UDP1234。未更改主机网卡配置。
  SendARP、四次 ping、十种 UDP 长度及共享复位后重新协商/ARP 均通过。

## 并发测试范围与已发现的限制

立即在共享 PHY 复位后进入并发测试，多次捕获 UDP 超时。失败仍按失败记录，
没有重试丢失的数据报作为成功，原始 UART/JSON 现场保留。超时时 USB 仍已枚举、
OHCI frame 正常推进，Ethernet 100M 全双工；CRC/preamble/MDIO 错误、SD 错误、
LCD 欠载和静音音频 DMA 错误均为 0，RX 槽丢弃计数增加。

只在直连 USB 网卡上的抓包显示，失败请求已发出但无回复；该请求前的 ARP
与 IPv6 广播/组播间隔约 60 µs。复位会触发本机 DHCP、IPv6、mDNS、LLMNR、
SSDP、IGMP 等初始化流量。两个未过滤的 RX 槽及合作式 CPU 处理无法保证这类
突发下任意数据报不丢失；这也不是 USB 接收器消失或整板停止运行。
未逐帧证明所有槽丢弃均属于后台包，因此不能宣称网络零丢帧。

独立示例有界地处理 RX 队列并及时提交回复，音频只生成 ring 新空出的采样数。
这些改动减少无用计算和排队，不消除两个 RX 槽的突发容量限制。
另做显式 30 秒 post-reset settling interval 后的五分钟稳定运行验收，
该等待不计入 soak；脚本及报告记录该条件，不能用稳定窗口结果覆盖复位早期失败。
本轮不修改网卡、增加大量 buffer 或改变 Ethernet HAL 的所有权模型。

稳定窗口 **300.015 秒、300 轮、7,103 次完整 UDP 回显、零 UDP 超时**。
有效 UDP 载荷合计 3,129,219 字节，最大实测 RTT 62 ms；这是合作式验收
工作负载的延迟，不是线速或纯 MAC 性能。测试含十种 UDP 长度、每轮
DDR 绘图/VBlank 换帧、`RVTEST00.BIN` 4,096 字节读取/CRC32 `08040e1e`、
46,875 Hz 静音 PCM DMA，以及 USB HID 存在/错误计数/OHCI frame 检查。

窗口首尾 RX 217→7,326、TX 21→7,092；audio played 28,193→14,040,612、
fetched 28,705→14,041,124；LCD frames 108,936→126,854、completed
102,563→120,481。采样不恰好落在窗口端点，不能据此给出精确频率。
USB 始终保持 `046D:C52B` / 3 HID 接口，PHY/controller、raw/key queue
错误为 0，OHCI frame 在 16-bit 回绕后继续推进；本轮没有真实按键报告。
SD errors、LCD underflows、audio underrun/overrun/errors、Ethernet
CRC/preamble/MDIO、UART drops 和 unhandled IRQ 全部为 0。

**原始 RX 丢弃 15→18，增加 3 帧**，不等于网络零丢帧。30 秒等待期间
RX 19→206、槽丢弃 4→15，明确发生于稳定窗口之前。该测试只证明
上述稳定窗口及有限的启动/恢复请求通过，复位早期突发丢 UDP 的限制仍存在。

## 最终安装与基础启动回归

最终 FPGA 配置使用 Gowin 普通 exFlash Erase/Program，配置结束地址
`0x0DD800`，随后 Reload；匹配的基础应用安装到 Flash `[2,4)` MiB。
没有执行 Verify、写后读回或配置 CRC 比较，正常装载镜像时的 CRC 保留。
Flash 启动、UART 加载执行及两次软件复位自动 Flash 启动全部通过。
每次轮询到 USB `046D:C52B` / 三个 HID 接口、Ethernet 100M full duplex、
相同 UID 派生 MAC；SD ready、LCD underflows=0、音频 control=4 / level=0 /
amp=0 / errors=0，目录读取和基础 help 命令通过。

板上最终运行基础 monitor：RGB LCD 黑色画布，SPI LCD 使用系统状态页、
实际 MAC 与 IP UNCONFIGURED，音频默认静音。独立示例和临时诊断没有
放入 ROM 或基础命令集。没有新增断电、拔插或目视确认要求。

## 仿真、软件检查与未验收项

`sim/test_usb_phy.py` 使用实际初始化 FSM 验证寄存器读 turnaround/NXT low、
DIR 抢占后重试、全部写操作 STP 终止，以及 six-pin serial 输出。
`sim/test_usb_ulpi_timing.py` 在两个量化传播角检查真实 FSM 的边沿交互；
行为模型不能替代 FPGA STA 或 USB 电气测试。
Wishbone 寄存接口、SD descriptor 背压/块计数/abort、共享 DDR 端口、
音频位序/DMA/取消、Ethernet 槽所有权及同步器保留仿真均通过，Python 语法检查通过。
公共 USB HAL 头的 RV32 C++17 编译及主机镜像格式边界测试通过。
480×272 扫描、同步/像素顺序以及注入欠载后的恢复仿真通过。

按用户要求延期：真实键盘按下/释放、修饰键/组合键、键盘 LED 反馈以及本轮
屏幕目视确认。USB 没有拔插验收；没有重新做断电检查或听音验收。
当前 PHY 配置仅 FS；LS/HS、Hub、USB 存储、鼠标实物以及任意 Report Protocol
键盘解码均不在本轮已验收范围。Boot 鼠标和异步 LED API 已编码，实物验证待后续。

原始证据在忽略提交的 `build/m9/`：`validation.json`、Programmer 日志、
`usb-enumeration.json` / UART 日志、`ethernet-verification.json` / UART 日志、
`soak-failed*` / `soak-captured-failure*`、`reset-traffic.pcapng`、定向 service-probe
及临时诊断数据；持久安装/恢复另有 `configuration-programmer.log`、
`configuration-reload.log`、`boot-upload-uart.log`、`base-startup.json` /
`base-startup-uart.log`。构建日志在 `build/m9-build.log`、`build/m9-demo-build.log`。
