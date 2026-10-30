# AE350 Ethernet

当前板级入口是 Dock RTL8211FI 的 PHY 管理面：APB `0xf80a0000`，独立 25MHz 参考输出，复位与 Clause22 MDIO。sys/AHB/APB 保持 50MHz，CPU800MHz、mem100MHz、DDR CK400MHz。

## 接线

用户确认的三条链路分别记录：

| 板卡接口 | 本机连接 | Linux 映射 |
| --- | --- | --- |
| RJ45 千兆口 | eth2 | `enp4s0`，MAC `60:be:b4:09:b5:f6`，载波与千兆全双工已确认 |
| 第一个 SFP+ | 万兆口1，万兆光模块 | `enp1s0f0`，Intel FTLX8571D3BCVIT1，模块 EEPROM 已读回 |
| 第二个 SFP+ | 万兆口2，千兆电模块 | `enp1s0f1`，OEM SFP-GE-T，模块 EEPROM 已读回 |

本机 `enp2s0` 已有 `192.168.10.169/24` 网络，不用于板卡直连测试。首轮调试对象为 RJ45；SFP+ 的 SerDes/PCS 与模块类型独立验收。

| PHY 管理信号 | FPGA 引脚 |
| --- | --- |
| reset_n | E17 |
| 25MHz reference | E18 |
| MDC | K23 |
| MDIO | K22，开漏释放高电平，板上上拉 |

板上参考来自厂商 `udp_rgmii_send` 示例和 Dock 原理图。PHY 地址由诊断扫描全部32个地址取得，不依赖固定地址假设。

## 管理寄存器

| 偏移 | 行为 |
| --- | --- |
| 0x00 | 只读 ID `0xae350e01` |
| 0x04 | 控制 bit0 reset_n、bit1 MDC、bit2 MDIO 拉低；其余位必须0 |
| 0x08 | 同步后的 MDIO 输入，bit0 |
| 0x0c | 参考频率，25000000Hz |

复位默认保持 PHY 复位；软件先等待参考稳定，释放复位后读取 IEEE ID1/ID2 和 BMSR。MDC 的软件高低相位均至少1µs。BMSR 连读两次消除 latch-low 状态，再记录链路和协商完成位。

## 工作流

构建和测试均使用 `mini.py`，重复构建沿用 `default` 工作区：

- `build gateware --machine mega138k_ae350 --synthesize`
- `build payload --machine mega138k_ae350 --source firmware/diagnostics/mega138k_ae350/ethernet/main.c`
- `board ethernet-suite --build <gateware-result.json> --payload <payload-result.json> --port <UART> --cable-serial <FPGA serial> --cpu-jtag-location <CPU serial> --bootrom-install <installed-boot-receipt>`

测试使用 SRAM/UART，核对完整已安装启动程序并保存 PHY 地址、ID、ACK 和 BMSR；不写 Flash。PHY 管理通过不计 MAC 收发通过。

## 当前状态

PHY 管理实板通过：`build/runs/20261028T124600159189Z-board-ae350-ethernet-suite/result.json`，ID `0x001cc916`，BMSR `0x79ad`（link/autoneg complete）。地址0和1均响应同一ID，数据驱动优先使用非零地址1。主机 `enp4s0` 同时确认1000Mb/s、Full、Link detected yes。最初读操作多采一个周转位导致左移，已修复；两个实际C读取函数都有64时钟读帧和16位数据回归。

管理候选 `build/runs/20261028T122357671267Z-build-ethernet-mdio-retimed-fabric-5b08b0/result.json` 的 setup/hold 为0/0，最小脉宽余量1.119ns。此前几组布局/布线未收敛，增量匹配仅44.71%；最终缩短 SD 超时、DDR HRDATA 和视频帧尾组合路径后通过。DDR有效传输/突发/CDC、SD逐周期接收等价（含超时0/1/2）、视频运行时回归通过。DDR实板 `build/runs/20261028T125710998025Z-board-ae350-ddr-suite/result.json`：256KiB、10检查、0失败。

当前后端改为 DDR DMA 描述符环，旧 PIO 字节队列已移除。硬件自动读取描述符、搬运帧数据并写回完成状态；CPU 仅发布/回收描述符，通过借用缓冲区指针处理报文。TX 的完整帧缓冲仍由 MAC store-and-forward 提供，RX 使用内部暂存隔离不可暂停的物理接收，不对 CPU 暴露数据 FIFO。

## DMA 环合同

TX/RX 各自配置 2..256 项的二次幂环，描述符独占一条32字节 cache line。8个小端32位字依次为 buffer、capacity、length、flags、cookie、reserved[3]。buffer 和 capacity 均按32字节对齐，capacity 为32..1536，长度最大1530；范围必须完整位于 DDR 的 `[0x100000, DDR_SIZE)`。描述符环与缓冲区互不重叠，由 BSP 初始化时检查。

flags：OWN=bit31、DONE=bit30、ERROR=bit29。软件同步数据/描述符后才发布 OWN；设备只消费 OWN 项。TX 完成表示整帧已复制进 MAC，可归还 CPU 缓冲区，并不表示已上线路。RX 的所有 payload 写均收到 DDR 接口接受确认后，才写回长度、清 OWN 并推进 RX producer。错误描述符不触发越界数据访问。索引为32位单调计数，按 ring mask 索引；满环不覆盖尚未回收的 RX 项。

| 偏移 | DMA 控制寄存器 |
| --- | --- |
| 0x10 | 后端 ID `0xae350e03` |
| 0x14 | MTU 1530 |
| 0x80 | control：bit0 enable；bit1 idle reset |
| 0x84 | status：bit0 busy、bit1 RX pending、bit8 fault |
| 0x88 / 0x8c | TX / RX 描述符基址，仅禁用并排空后可改 |
| 0x90 | ring mask（项目数减1） |
| 0x94 / 0x98 | 软件 TX producer / 硬件 TX consumer |
| 0x9c / 0xa0 | 硬件 RX producer / 软件 RX consumer |
| 0xa4 / 0xa8 | IRQ pending W1C / enable：TX=1、RX=2、error=4 |
| 0xac | 故障代码；OWN 协议错误会停止新工作 |

IRQ 接至 fabric 扩展中断 bit9。BSP 当前使用完成索引轮询；ISR 尚未实板验收。禁用后继续排空已接受的事务和当前帧，不启动后续 TX 项；busy 清零前 CPU 不能收回缓冲区。停止超时不会声明所有权已归还。无标签 DDR 读不取消，迟到响应仍属于原请求。

CPU/视频与网络通过单级三客户端轮转仲裁共享256位 DDR UI，读响应保留原拥有者，写命令与写数据成对接受。描述符与数据使用现有32字节 cache clean/invalidate；发布前再通过描述符读回屏障，保证 CPU AHB 暂存写已排空。软件只可访问自身拥有的整条 cache line。

硬件 MAC 生成/检查以太网 CRC/FCS；IPv4、ICMP、UDP checksum 仍由软件检查，不宣称 L3/L4 checksum offload。测试载荷使用4项环，TX/RX 缓冲区均1536字节，验证多次回卷和缓冲尾部保护。

## 验证进度

PIO 基线 `fb3c74f8dce4` 的完整时序通过（setup/hold0/0，TX偏斜最大0.015ns），并实测 ARP、ICMP负载0/1/31一次通过；ICMP56失败且 RX CRC 错误7次。该结果仅作历史基线，不计 DMA 或完整网络验收通过。DMA 切换不被视为已修复这一接收错误。

DMA RTL 仿真覆盖 CPU/视频/网络三客户端并发、TX/RX、二项环回卷、1101字节跨32个 DDR beat、尾字节 mask、非法缓冲区、CPU并发访问及读响应拥有者。MAC 字节流回环验证前导码、补齐和 CRC。DMA 位流 `8ede3c96f4a4` 已通过完整时序（setup/hold 0/0，最小脉宽余量 +0.705ns，TX 偏斜最大 0.015ns）。DDR 实板回执 `build/runs/20261029T120423591705Z-board-ae350-ddr-suite/result.json` 验证256KiB、10检查、0失败。

DMA 网络回执 `build/runs/20261029T120601975712Z-board-ae350-ethernet-suite/result.json` 中，ARP、ICMP、UDP共49用例最终全部得到预期结果，包含1472字节负载及两个坏checksum拒绝用例；但有7次重试，RX CRC和帧错误各7次，因此整体仍为失败。TX producer/consumer均47，RX均70，DMA fault、丢帧、API错误与尾部保护错误均0。尚未验证吞吐率和中断处理程序。

MAC 格式化与完整TX帧存储运行于sys50/32bit；跨到125MHz后由8项寄存器CDC队列和32→8位串行器供给PHY。该小队列明确约束为寄存器，避免125MHz BSRAM最小脉宽不满足。

RGMII 使用 PHY 内部 TX/RX 各2ns延迟，合格DMA基线的 FPGA RX 数据延时为2000ps；2500ps候选仍在构建/验证中，不视为已修复CRC。BSP 只操作匹配的 RTL8211F，保留厂商页并读回确认；依据 [Linux Realtek PHY 驱动](https://kernel.googlesource.com/pub/scm/linux/kernel/git/torvalds/linux/+/d4c08d8b23b22807c712208cd05cb047e92e7672/drivers/net/phy/realtek.c)。TX 采用布线后时钟/数据偏斜与占空比门禁，要求完整的 DDR 边沿和转换覆盖。

AHB 地址和返回均由寄存级持有，错误响应保持两阶段 ERROR；AHB 突发、写合并及恢复38项回归通过。RX packet FIFO 与 CDC 之间增加反压寄存缓冲。接收暂存采用四个8位 RAM bank，硬件组装256位 DDR 写数据。


2026-10-30 对照：在同一合格 `8ede3c96f4a4` 位流上，关闭 PHY RX 2ns 延时（仅诊断载荷定义 `AE350_ETH_PHY_RX_DELAY=0`）后，ARP三次无回复，CRC错误13次，回执 `build/runs/20261030T045522014801Z-board-ae350-ethernet-suite/result.json`。默认仍启用该延时。恢复默认后，回执 `build/runs/20261030T045838280379Z-board-ae350-ethernet-suite/result.json` 在 UDP1339 字节负载处三次失败；CRC错误4次，DMA fault/丢帧/API/保护错误均0。PHY标准reg10测试前后均为 `0x7800`，两个读取ACK均成功，idle error计数0；此结果支持继续检查 PHY 到 FPGA 的接收采样，但尚不确定根因。

`tests/mega138k_ae350/ethernet_mac_verilog_test.py` 的真实 MAC32+CDC+PHY8 RTL回环通过10帧（含1514字节），检查独立软件FCS、补齐、帧间隔、无帧中断流、CDC回卷与接收反压稳定性。仿真将无组合环的AST按拓扑顺序生成单一组合块，避免 Icarus 对多个块中间默认值的零时间反复调度；不修改生产RTL逻辑。


RX2500 的 place3/route1 候选 `build/runs/20261030T045204899280Z-build-ethernet-dma-rx2500-route1-26a4e2/result.json` 完成布线但时序失败（setup66、hold0），主要为DDR仲裁握手到AHB写计数/超时计数，以及硬核HWDATA到请求FIFO的路径；未编程该候选。后续在同一 `gateware/default` 工作区使用 place4/route2 重建，回执 `build/runs/20261030T051451418221Z-build-ethernet-dma-rx2500-place4-38cd6b/result.json`。匹配的网络诊断载荷为 `build/runs/20261030T050425316508Z-build-ethernet-dma-rx2500-phy-status-d87df1/result.json`（`eb3d83b7bb53`）；新位流未通过完整时序前不得上板。
