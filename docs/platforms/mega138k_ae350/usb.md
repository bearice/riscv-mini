# AE350 Soft USB Host

Dock 唯一 Soft USB 口连接独立 60MHz 全速 PIO Host，APB 控制面保持 sys50，通过 AXI-Lite 异步 FIFO 访问 USB 域。时钟取自机器 `clocks.usb`，PLL 使用 50MHz ×24 ÷20，VCO 1200MHz。固件复用 TinyUSB Host/HID/Hub 和 PIO HCD，诊断程序采用轮询；当前没有 BIOS 键盘输入集成，不宣称 LS 或 HS 支持。Linux 使用共享原生 HCD（见[Linux 驱动](#linux-驱动)），经全速 Hub 支持存储类与 HID。

## 板级连接

发送 D+/D− 为 M21/M22，保留 42Ω 串联电阻；接收端直接由单端 LVCMOS33 输入引脚采样（U21 为 D+，T20 为 D-）。所有 USB 相关引脚（`M21, M22, U21, T20, V21, U20, M19, T19, U19`）在 `board.json` 中均显式配置 `PULL_MODE=NONE`，防止高云 PnR 对未引出引脚默认使能弱上拉（否则 T20 会对抗外置 15kΩ 下拉将 D- 拉高至非法 SE1 状态）。M19 的 Device 上拉、T19/U19 的零欧 termination 支路以及 V21/U20 均保持高阻，Host 下拉使用实板外接的 15kΩ 电阻。

实板测量：插入 Full-Speed 设备后，D+ 电压约为 2.8V，D- 电压为 0V，符合全速 J 状态要求。此前供电故障由实板 D8 反装引起，已短接修复。原理图规定方向是板上 +5V → FB4 → D8 → FU1 → J5 VBUS。

## 寄存器

基址 `0xf8060000`；32bit 对齐访问，写入必须完整字节选通。原生控制器 0x00–0x20 使用仓库 `usbh_host_defs.v` 合同，APB 请求等待对应 USB 域响应，AW/W 独立握手。

| 偏移 | 只读诊断 |
| --- | --- |
| 0x100 | 本机接口 ID `0xae350c01` |
| 0x104 | bit0 原始 D+ 比较器、bit1 原始 D− 比较器、bit2 发送使能、bit3 PLL locked；逐位同步至 sys，用于静态电平诊断 |
| 0x108 | USB 时钟 Hz |

诊断寄存器不依赖 USB 域响应。诊断区写入和未知偏移返回总线错误。控制器 STATUS 的低两位在发送时可能反映自身发送状态，不能直接替代原始输入诊断。

## 构建与验收

构建统一使用 `mini.py build gateware --machine mega138k_ae350 --synthesize`，重复构建复用 `default`。USB RTL、PHY 与控制器源码纳入构建输入，USB 寄存器合同纳入 image ABI。

诊断入口为 `mini.py board usb-suite`，显式选择 `--build` 和 `--payload`，并提供串口、FPGA cable serial、CPU JTAG，以及兼容的 `--bootrom-install` 安装记录。入口只编程 SRAM，通过 UART 装载载荷，在 loader 空闲时用 CPU JTAG 读回完整启动程序，随后释放 CPU 连续运行并保存 UART 结果。无 Flash 写入。载荷源码目录为 `firmware/diagnostics/mega138k_ae350/usb`，使用统一 payload 构建入口加入 TinyUSB 公共、Host、Hub、HID 和共享 PIO HCD 源码。

`passed` 判定要求载荷开始标记、完整枚举成功结束标记、实际 VID/PID 和 HID 接口。HID 报告数量单独记录，不代替物理按键验收；断开重连和重启重复性另行验证。

## 当前证据

首轮候选 `build/runs/20261028T045605853957Z-build-usb-host-abed31/result.json` 完成 PnR：setup/hold 0/0，最小脉宽余量 1.119ns。跨时钟读写仿真、PHY 48/60MHz 相位和 0/8ns 抖动仿真通过。

首轮实板 `build/runs/20261028T051300276624Z-board-ae350-usb-suite/result.json` 启动镜像完整读回、POST、DDR 初始化和 UART 应用运行完成，但 USB 枚举失败：60秒内 STATUS 低两位持续 SE0，无 VID/PID、HID 或报告。这不能证明设备未连接，也不能单凭它认定电路损坏。原始输入诊断候选 `build/runs/20261028T051955148868Z-build-usb-host-raw-status-93fd6e/result.json`（gateware `3adb8290ab05`）同样通过 setup/hold 0/0、最小脉宽余量 1.119ns。该候选包含完整 USB 源码依赖与 USB image ABI。

第二轮实板 `build/runs/20261028T074721335272Z-board-ae350-usb-suite/result.json` 使用载荷 `build/runs/20261028T052138755328Z-build-usb-host-278844/result.json`，60秒枚举失败。日志持续 `CTRL=0xe8`、`RAW=8`：PLL 锁定、TX 未驱动、两个输入比较器均为低。这排除了 FPGA 主动压低总线的解释，但不能区分外部数据线实际为低与比较器输入问题。接收器插入时的 VBUS/D+/D− 实测电压和实际下拉阻值待用户提供；真实输入、重启重复性和拔插恢复尚未通过。

第三轮实板验证：将接收改为单端 LVCMOS33，并在 `board.json` 将相关 9 个引脚配置为 `PULL_MODE=NONE`，载荷重新编译为当前 ABI（`0x0952cb09`）。上载后实测状态为 `RAW=0x00000009`（J 状态稳定），成功识别并枚举 Logitech Unifying 接收器（`VID:PID 046d:c52b`），正确识别并挂载 3 个 HID 接口，最终打印 `USB ENUM PASS`。

## Linux 驱动

共享驱动 `firmware/linux/riscv_mini_usb.c` 以 `riscv-mini,ae350-softusb` 匹配 DTS 节点 `usb0`（基址取地址合同 `AE350_USB_BASE`）：探测核对诊断页 ID `0xae350c01` 与 PLL 锁定位，20K 的 `riscv-mini,liteusb` 仍走共享 PHY 复位序列。控制器中断未接 PLIC，驱动为轮询 PIO（不设 `HCD_DMA`，内核不做 DMA 映射），只支持全速、非同步传输。

调度规则：

- 同一端点的 URB 必须按提交顺序完成。`usb_sg_wait` 会为一个端点同时排入多个 URB，若按包轮转会把数据交错（U 盘读出无 MBR、写回读不一致）；驱动只运行每个端点最早的未取消 URB。
- 一个工作项连续发送同一 URB 的最多 16 个包，NAK 或完成时让出。bulk/control 的 NAK 先立即重试 15 次，之后每 tick 一次；HCD 不对 NAK 设超时，URB 超时由类驱动负责（此前 1000 次 NAK 上限会使 usb-storage 在 U 盘长写忙时复位设备）。
- 包完成判定：先等 RX_STAT 的 START_PEND 清零（SOF 保护带内保持），隔 1µs 跨时钟域后再等 IDLE；不再每包固定睡 1ms（改前 U 盘读约 29 KiB/s，理论上限 64 KiB/s）。

实板 `build/runs/20261102T162127780295Z-board-ae350-linux`（gateware `20261102T153943792093Z…`）：Genesys 05e3:0606 Hub 下 U 盘 090c:1000 顺序读 496–596 KiB/s、写 278 KiB/s，末尾 256 KiB 保存/改写/回读/恢复一致，FAT 只读挂载可列根目录；Logitech 接收器 3 个 HID 接口注册。验证入口为 `board linux --devices`。

包重发：驱动对同一 PID/toggle 最多发 5 次（设备丢弃重复包），每次失败以 `PID xx dev n ep n attempt k: RX_STAT … (no reply|CRC error|oversize)` 限速告警记入内核日志，5 次都失败时 URB 以 `-EPROTO`（类驱动报 -71）结束。

丢握手诊断：旧 gateware（`20261102T180458616948Z…`）配诊断内核的 3 次 `board linux --devices`（`20261102T195009475795Z`、`20261102T195210251097Z`、`20261102T195359022778Z`）共 21 次重发，全部是发往 YubiKey（dev 4）ep0 的 SETUP 无应答，连续 3–5 次成串出现，起点都在设备编号分配后约 100ms（SET_ADDRESS 之后第一个请求）；其中一次 5 次全失败报 -71，第二次枚举成功。Hub、U 盘与 Logitech 接收器的包和所有 IN 数据无 CRC 错或超时。同一包重发连续失败说明与包尾时序相关而非随机噪声。

EOP 修正：上游 `usb_fs_phy` 在 SE0→J 时即释放总线，J 只靠 1.5kΩ 上拉被动恢复；USB 2.0 §7.1.13.2 要求发送端驱动 1 bit J 后再高阻。PHY 新增 `STATE_TX_EOP3`，驱动 J 一个 bit 后才开放接收。`tests/primer20k_vexriscv/usb_fs_phy_tb.v` 让 PHY 回读自身驱动并检查 EOP 为 2 bit SE0 + 1 bit 驱动 J，随后接收设备回复，48/60MHz 与抖动配置均通过（旧 PHY 报 `EOP SE0 10 / driven J 0 clocks`）。YubiKey 缺失的应当是主机在 SET_ADDRESS 状态阶段发出的 ACK：设备未见 ACK 则不切换地址，随后发往新地址的 SETUP 无应答。

修正后 gateware `build/runs/20261102T194334412350Z-build-usb-eop-driven-j-bf4108/result.json`（`7522f3e526c7`，setup/hold 0/0，最小脉宽余量 0.1ns，image ABI 与旧版相同，沿用已安装 BIOS）以相同内核、U-Boot 和载荷连跑 3 次 `board linux --devices`（`20261102T200546620663Z`、`20261102T200718150537Z`、`20261102T200859479060Z`）：重发 0 次、无 -71，YubiKey、Logitech 接收器与 U 盘每次枚举，U 盘读 567–584 KiB/s、写 275–277 KiB/s，末尾改写/回读/恢复一致。丢握手问题按此关闭；gateware 写入 sysflash 仍需明确授权。共享驱动的包等待与调度改动及 PHY 修正尚未在 20K 实板复验。
