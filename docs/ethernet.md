# Ethernet 与 PHY HAL

Dock 3713 的 RTL8201F 通过 RMII 接入 LiteEth。系统保持 CPU/Wishbone 60 MHz、DDR CK 120 MHz，PHY 向 FPGA A9 输出独立 50 MHz REF_CLK，收发通过异步 FIFO 跨域，不增加 PLL。当前只宣告并支持 **100 Mbps 全双工**；不支持 10 Mbps、半双工或碰撞处理。

| 信号 | FPGA 引脚 |
| --- | --- |
| REF_CLK input | A9 |
| RXD0 / RXD1 | F15 / C9 |
| CRS_DV / RX_ER | M6 / L8 |
| TXD0 / TXD1 / TX_EN | D16 / E14 / E16 |
| MDC / MDIO | F14 / F16 |
| Ethernet + USB RESET_N | F10，唯一的 `phy_reset` GPIO 所有者 |

F14/F16 与 HDMI DDC、部分触摸/摄像头 I2C 角色冲突；当前 RGB LCD 像素引脚不冲突。未来使用这些复用接口时需明确选定所有者。

## 硬件与时钟

MAC 为 32 位 CPU Wishbone 接口，接收/发送各两个 2 KiB 槽，合计 8 KiB 地址空间。RX `0xb0000000..0xb0000fff` 只读，TX `0xb0001000..0xb0001fff` 只写，均为 IO region。实际逻辑使用 LiteEth 1530 字节槽深度；HAL 限制不带 VLAN 的 14..1514 字节 Ethernet 帧。网络包缓存放在片上 RAM，网络控制器本身不增加 DDR DMA。

TX 在 REF_CLK 上升沿输出；RX 输入寄存器在下降沿采样，内部 RX 逻辑在上升沿处理，相关半周期路径正常计时。收发固定 100M，避免 LiteEth 自动检测首个前导码前按 10M 运行而丢失首包。适配点针对锁定的 LiteEth 版本检查唯一的 speed selector；上游接口变化会明确停止构建。

Gowin `syn_keep` / `syn_preserve` 保留 MultiReg 两级触发器，避免宽位同步器推断为 LUT RAM。约束仅豁免异步输入的第一级 D，以及异步复位同步器的 PRESET，第二级、各域内部路径及半周期 RX 路径保留时序检查。50 MHz 输入有独立周期约束。

外部时序预算：TX setup 4 ns、hold 2 ns，另留 0.5 ns 板级预算；RX 使用输入延迟 min 1.5 ns、max 10 ns。RTL8201F 手册给出 RX output delay 最小 2 ns，未给最大值，因此 max 10 ns 是当前工程预算，不能据此宣称所有板卡/温压下的完整接口时序保证。依据见 [Realtek RTL8201F rev1.4 数据手册](https://www.unikeyic.com/media/datasheet/c2/44/6b78/c2446b789a9b56c262fc380ba352114e.pdf)，§7.16、§9.2.3。

## 软件接口

`hal_init()` 通过公共 F10 reset 初始化 Ethernet。MDIO clause22 扫描优先地址 1..31、最后 0，避免优先使用地址零的广播别名。匹配 RTL8201F family ID `0x001cc81x`，保留厂商 RX/TX timing offsets，启用 RMII/REF_CLK output，禁用 LDPS、EEE advertisement 和时钟 SSC，然后只宣告 100M full duplex、重启协商。读 MDIO 时在 MDC 上升沿之前取样；上升沿后取样会读到下一位。每半周期至少 1 µs，软件/IRQ 开销会进一步降低 MDC 频率，未宣称固定 500 kHz。

| API | 语义 |
| --- | --- |
| `hal_eth_init()` | 有界设置、PHY ID/参考时钟检查；成功不代表协商已结束 |
| `hal_eth_stop()` | 屏蔽 IRQ，复位 MAC、packet slots、CDC/PHY 逻辑，取消旧帧所有权；不拉低 F10 |
| `hal_eth_poll()` | 250 ms 轮询两次 BMSR、BMCR、partner，更新链路；由 `hal_poll()` 调用，不消费包 |
| `hal_eth_get_info()` | ID、地址、link、speed、duplex、寄存器、启动时参考时钟估计、包/错误/IRQ统计 |
| `hal_eth_send(frame,len)` | 复制到 TX SRAM 后提交；仅允许一帧在途，BUSY 需稍后重试；无链路返回 NO_MEDIA |
| `hal_eth_receive(frame,cap,&len)` | 空队列 BUSY；完整复制后 W1C 释放槽；无效/放不下的帧释放后返回 INVALID |
| `hal_eth_mdio_read/write()` | 原始 clause22 寄存器接口，仅供主循环使用；调用者自己管理 page select，使用后恢复 page0 |

帧包含 Ethernet 头，排除前导码、SFD、FCS。MAC 自动添加 padding/FCS、检查 RX FCS；HAL 不做目的 MAC/IP 过滤，也不包含协议栈。应用须及时取走或丢弃帧，否则两槽满时新包计入 `rx_drops`。基础 monitor 不消费网络包，这是原始帧 HAL 的预期行为。

RX IRQ 只屏蔽持续有效的 RX 通知，不确认释放包；主循环复制后确认并重新允许通知。TX IRQ 确认完成、归还发送槽。`tx_frames` 为成功提交到 MAC 的帧数，完成通知表示 SRAM 内容已被 MAC 消费，不能当作线端送达或对端 ACK。

所有 API 为单主循环所有者；ISR 不做文件系统、拷包或 MDIO。`hal_phys_reset()` 先停止 Ethernet，F10 拉低至少 10 ms，释放并等待恢复后重新初始化；这也复位 USB PHY，M9 需接入 USB Host 的停止和重建逻辑。`hal_reboot()` 先停止 Ethernet/音频/视频再软件复位。

## 独立验收程序

`firmware/examples/ethernet_demo.c` 在 DDR 中实现最小 ARP、IPv4 ICMP echo、UDP 1234 echo，板地址 `169.254.20.20`、MAC由Flash工厂UID派生。不包含 TCP/DHCP/lwIP。只接收 IPv4 IHL5、未分片包，检查 IP/ICMP/UDP 校验和和长度；不支持 options、fragment 或 VLAN。ARP/ICMP/UDP 回显只用于本机直连验收，主应用和 bootloader 不链接这些协议。

程序先完成SD/LCD初始化，再启动网络以避免启动期间填满槽；主循环、音频预填充、SD CRC、绘图和较长的串口状态输出均服务网络，绘图/状态路径也补充静音音频ring。250 ms链路轮询尚未确认时保留待发ARP回复，恢复后重试。串口 `s` 状态、`d` 静音 DMA、`x` 停音频、`r` 只读 SD 文件 CRC、`f` 换帧、`e` 公共 PHY reset、`!` reboot。

```powershell
.venv/Scripts/python.exe scripts/build.py --synthesize --output-dir build/m8
.venv/Scripts/python.exe scripts/build.py --app firmware/examples/ethernet_demo.c --output-dir build/m8-demo
.venv/Scripts/python.exe sim/test_ethernet.py
# 先查看实际 USB 网卡名称、ifIndex、已存在的 IPv4；不要复制另一台机器的值。
Get-NetAdapter
Get-NetIPAddress -AddressFamily IPv4
.venv/Scripts/python.exe scripts/ethernet_verify.py --program --host-ip 169.254.48.203 --interface-index 79 --soak-seconds 300
```

脚本绑定指定的现有IP和Windows IP_UNICAST_IF，并在启动/共享复位后通过源地址限定的SendARP解析邻居，依次检查协商、UDP 长度0..1472、ping、共享 reset 后恢复，以及最多300秒的网络/SD CRC/LCD/静音音频并发。不修改主机 IP、路由或防火墙配置。五分钟结论与实物拔插结果见 [M8 验证](m8-validation.md)。

## Flash 身份与 SPI 状态页

`hal_flash_uid(uid,&length)` 返回工厂只读UID。本机XTX `0x0b4017` 使用 `5A 00 01 94`、一字节dummy、16字节UID；支持的Winbond型号使用`4B`、四字节dummy、8字节UID。XTX依据：[XT25F64B原厂手册 rev1.2](https://file2.dzsc.com/product/19/06/22/216185_132959081.pdf)，§6.35。拒绝全零/全FF或传输失败，不写Flash，不增加bootloader功能。

`hal_eth_init()` 对UID的线序字节计算FNV-1a64，取结果低六字节（little-endian），首字节清除group位、设置local位。`hal_eth_get_mac()` 返回派生地址；UID不可读时网络初始化失败，不回退到全板共享的固定MAC。派生46位地址有理论哈希碰撞可能，不作为注册OUI或安全认证。软件复位/F10复位重新读取同一工厂身份，不依赖应用区内容。

SPI屏显示完整MAC、IP和ETH LINK UP/DOWN，保留DDR/SD状态。独立示例拥有固定IPv4 `169.254.20.20`；基础应用没有IP栈，显示IP UNCONFIGURED。仅更新网络文本所在行，MAC用大写十六进制和冒号，IP用十进制和句点。

CPU拷包加两槽RX不能保证吸收任意100M突发，也没有硬件目的地址过滤。验收记录原始`rx_drops`起止值，UDP则要求每次请求完整回显、没有超时；两者不能等同。该示例吞吐不能当作100 Mbps线速验收，后续若需要线速/突发承载，应增加过滤、缓冲或DMA。
