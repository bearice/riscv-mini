# M8 Ethernet 验证记录

日期：2026-10-01。前置提交 M7 `be7975ce9955d7c0cbf28bfc3bd89e70be74dbf5`。实现 RTL8201F MDIO、LiteEth RMII MAC、原始帧 HAL、应用侧 Flash 工厂 UID 派生 MAC，以及 SPI LCD 地址/链路显示。ARP/ICMP/UDP 验收协议仍只在独立 DDR 示例中，基础 monitor 和 bootloader 不链接 IP 协议栈。

## 配置与构建

| 项目 | 结果 |
| --- | --- |
| CPU / Wishbone / DDR | 60 MHz / 60 MHz / CK120 MHz，DLL-off CL6/CWL6 |
| Ethernet | 100 Mbps 全双工；PHY 输出50 MHz REF_CLK，RX下降沿输入寄存、内部上升沿处理 |
| MAC SRAM | RX/TX各2槽，每槽地址跨度2 KiB；CPU拷贝，无网络DDR DMA |
| PHY | ID `001cc816`，地址1；地址0亦有广播别名响应，因此优先扫描1..31 |
| 默认 ROM / SRAM / boot.bin | 8,192 / 8,192 / **6,568 字节**；未增加UID/网络/LCD代码 |
| 基础 app.bin / app.img / BSS | 29,512 / 29,560 / 6,396 字节，DDR执行 |
| 示例 app.img / BSS | 37,632 / 145,660 字节；包括128 KiB静音音频ring |
| ABI | 原生SD `8c34517e`；SPI回退 `ce997d2e` |
| Logic / Register | 12,614 / 20,736；6,706 / 16,173 |
| BSRAM / rPLL | **33 / 46；2 / 4** |
| PnR | setup 0、hold 0；异步首级/复位PRESET例外，第二级和半周期RX路径仍计时 |
| SPI-SD回退 | 生成/编译通过，app.img 29,224字节；本轮未重新PnR或切换实卡 |

相比M7增加1,214 Logic、742 Register、14 BSRAM，没有新增PLL。BSRAM增加值是实际Gowin推断/包装结果，不能只按8 KiB地址空间推算资源。RGB LCD仍9 MHz、SD四位7.5 MHz、SPI LCD6 MHz、Flash10 MHz、音频46,875 Hz/BCK1.5 MHz。

并发/拔插测试的 `build/m8/gateware/riscv_mini.fs` SHA256：`9fdf13cc89486e634acd4b0ceef902640648a3d430570fc1e3dc0a846ff5aedb`。

最终默认 `build/base/gateware/riscv_mini.fs` SHA256：`da7de600c62b2addb826f85095336afa0aaf14101d66f0ccf74492c62b910871`。

最终基础 `build/base/firmware/app.img` SHA256：`13ab6f3e7d9950e42bb7e2410cdb8cf9a14a074ceab145ced9b124e2a2d727b3`。

两次构建生成RTL只存在日期和结尾注释差异，boot.bin完全一致；最终默认构建单独PnR并进行启动回归，没有把最终基础版再跑五分钟。

## Flash UID 与地址

本机Flash JEDEC `0x0b4017`，工厂UID为 **`41503446333236000000000200280050`**，派生本地单播MAC **`8E:6F:09:E9:70:28`**。按UID线序字节作FNV-1a64，取低六字节，清group位、置local位。主机用独立Python计算核对派生结果，并通过源接口限定的SendARP核对实际回应地址。

公共F10复位会重新读取UID并派生地址，复位后ARP/UDP恢复且MAC相同。UID读取只读工厂区域，不写Flash；读失败拒绝初始化Ethernet，无固定MAC回退。算法的46位地址空间有理论哈希碰撞，不能宣称注册OUI或安全身份认证。

独立示例IPv4 `169.254.20.20`、UDP1234；SPI LCD显示上述MAC、IP与ETH LINK UP/DOWN。用户明确确认地址完整正确，两块屏幕正常稳定。基础系统只提供原始帧HAL，无IP栈，因此SPI屏显示相同MAC和 **IP UNCONFIGURED**，不会把示例地址当作已配置的系统IP。

## 仿真与实际收发

- `sim/test_ethernet.py` 使用实际LiteEth Wishbone SRAM接口，验证小端CPU字、两RX槽所有权、满队列不覆盖旧帧、CRC/越界丢弃、两TX槽隔离、非整word尾部/背压，以及局部复位清空队列和统计。检查Gowin两级同步寄存器保留属性。
- 原有音频、DDR端口调度、480×272扫描/有意欠载恢复仿真通过；这些仿真不证明RMII/DDR电气时序。
- 实际RV32 CPU上的协议自检验证奇数/MTU长度、IP/UDP校验和、截断、分片拒绝及ARP。实际网络收发额外验证MAC自动padding/FCS和PHY路径。
- 直连本机 **Ethernet 7 / Lenovo USB Ethernet / ifIndex79**，现有IPv4 `169.254.48.203/16`，100 Mbps。源IP和IP_UNICAST_IF限定此接口；未修改IP、路由或防火墙。主网络Ethernet 5保持 `192.168.10.128/24` /10 Gbps。
- UDP载荷0、1、31、32、63、64、255、511、1024、1472字节逐字节回显通过；4次ICMP ping无丢失；共享F10复位、重新协商和ARP解析后再跑上述UDP长度全部通过。
- MDIO最初在MDC上升沿之后读入，ID呈现一位偏移；改为上升沿之前读取后稳定得到`001cc816`。RMII固定100M避免上游首个前导码前默认10M的首包损失。临时CSR/MDIO诊断命令已经移除。
- 合作式示例在音频预填充、SD CRC、绘图和长状态输出中服务网络；协商尚未被轮询确认时保留待发ARP回复，恢复后重试。HAL仍为单主循环所有者。
- Python语法检查、新公共HAL的RISC-V C++17头编译通过。

## 五分钟并发

**300.015秒、300轮、7,669次UDP完整回显，零UDP超时**。有效UDP载荷合计3,418,445字节，最大实测RTT141 ms；包含串口状态/绘图等待，不是线速吞吐或纯MAC延迟指标。持续静音音频DMA、CPU绘制/交换DDR视频帧、读取`RVTEST00.BIN`（4,096字节，CRC32 `08040e1e`）。

窗口首尾：RX32→7,824、TX21→7,652；audio played48,013→14,039,299、fetched48,525→14,039,811；RGB frames79,905→97,796、completed76,421→94,312。首尾采样不包含全部窗口端点，不据此计算精确设备频率。

全过程PHY link保持100M full duplex，CRC/preamble/MDIO错误、audio underrun/overrun/errors、SD errors、LCD underflows、UART drops和unhandled IRQ均0。用户确认SPI屏地址和两块屏幕稳定。

**原始RX槽丢弃计数2→52，新增50帧，不能写成“网络零丢帧”。** 两槽原始接收队列没有目的地址过滤，示例收到了大量不处理的背景包；空闲期间也观察到计数增长。但不能逐帧证明被丢弃者全部属于背景包。验收UDP采用逐请求等待、完整内容核对，无超时；这证明本轮测试流通过，不证明任意突发、广播负载或100 Mbps线速无丢包。后续更高负载需要过滤、更多缓冲或DMA。

## 网线恢复与最终安装

用户协助拔出Dock网线：HAL `link=0 / mbps=0 / full=0`，link_changes由1到2，本机USB网卡Disconnected；串口返回提示符，SD文件CRC和DDR换帧仍通过，LCD欠载0。用户确认SPI屏显示LINK DOWN。

插回后自动恢复100M全双工，link_changes增至3，SendARP仍返回`8E:6F:09:E9:70:28`。十种UDP长度各十次，共100次完整回显通过，3次ping无丢失、RTT1–2 ms；MDIO/CRC/preamble错误和LCD欠载均0。用户确认屏幕LINK UP恢复。插回后的原始RX槽丢弃计数68，保持上述突发承载边界说明。

最终默认配置已用Gowin普通exFlash Erase/Program写入并Reload，匹配的基础应用安装到`[2,4)` MiB；未执行Verify、写后读回或配置CRC比较。正常启动时bootloader的镜像/传输CRC保留。

最终硬件上，Flash应用加载、UART应用加载、Flash ID/目录/status/help，以及ROM错误头CRC、ABI、加载/入口边界、零长/过大镜像、flags、packet CRC、截断超时、payload CRC拒绝均通过。随后两次软件复位自动从Flash启动，实际MAC均保持`8E:6F:09:E9:70:28`，音频control=4、FIFO/amp=0，LCD欠载0；协商完成后均恢复100M全双工。PHY初始化不等待协商，因此启动初始status可以link=0，回归用30秒deadline轮询协商，不以固定0.5秒等待代替链路条件。

板上最终恢复基础monitor：RGB LCD黑色画布、SPI LCD显示实际MAC和IP UNCONFIGURED、音频默认静音。未追加断电检查或第二轮五分钟测试。

## 原始证据

忽略的 `build/` 保存 `m8/ethernet-verification.json`、`ethernet-verification-uart.log`、`ethernet-startup-probe.log`、`ethernet-hotplug.json`、`ethernet-hotplug-down/up-uart.log`、`hotplug.py`、`m8/validation.json`、`base/validation.json`、`m8-final-base-console.log`、`m8-demo-console.log`、`m8-spi-console.log`、`m8-final-flash-install-console.log`、`m8-final-boot-verify-console.log`、`base/boot-verification.json`、`base/m8-flash-auto-boot.json`及对应UART日志。接口与时序预算见 [Ethernet](ethernet.md)。本轮不声称10M/半双工、TCP/DHCP/lwIP、网络DDR DMA或USB Host已实现。
