# AE350 Soft USB Host

## OHCI 迁移实验

当前源码改用锁定依赖中的 `UsbOhciWishbone_Dw32_Pc1_Pf48000000` 单口 OHCI 核。
USB 时钟改为 48 MHz（50 MHz ×24 ÷25），APB 控制面仍为 sys50；控制寄存器
基址不变，标准 OHCI 寄存器位于页首，诊断身份改为 `0xae350c02`。
OHCI 的 32 位 Wishbone DMA 经过保留事务的适配器和异步 FIFO，作为第四个主端口
访问共享 256 位 DDR 接口；PLIC 使用 EXT_INT[4]（源 14）。字节写使能保留，
DDR 窗口外的 DMA 返回 ERROR。字位置偏移使用显式位拼接，避免生成 Verilog 时
自定宽乘法截断位移量，导致描述符写错字。

裸机枚举诊断目录仍为 `firmware/diagnostics/mega138k_ae350/usb`，改用 TinyUSB
OHCI HCD 与本地 `ohci_litex.h`；通过轮询运行，ROM 上传时缓存关闭。
它不验证 PLIC ISR，也不替代 Linux DMA 一致性验收。Linux 配置和 DTS 已切到
标准 `generic-ohci`，但当前内核默认一致性 DMA 与 A25 L1D 的组合尚待验证，
不能据裸机通过宣称 Linux 可用。当前实验不宣称低速、等时或高速设备验收通过。

首轮候选 `1ea6b2915846` 的完整 STA 通过，但实板枚举失败；HCCA 帧号写回正常，
描述符头遭覆盖。真实 OHCI 核加 DDR 适配器的 RTL 仿真复现此问题，并在位移量
定宽修复后通过。最终候选和实板结果以本节后续记录及 `build/runs/` 回执为准。

候选 `0c2366135c33` 完整 STA 通过，裸机枚举回执
`20261104T175147110556Z-board-ae350-usb-suite` 通过：设备 `090c:1000`、
`1050:0407`，两个 HID 接口，未验证物理输入。PLIC 源 14 的挂起、claim、
清除设备事件和 complete 流程通过，见 `20261104T175120108117Z-board-ae350-jtag`。
Linux 标准 `ohci-platform` 已绑定，根 Hub、外部四口 Hub、Yubico 和 Logitech
键盘/鼠标接口均已枚举；但存储尚未通过：
`20261105T035633157119Z-board-ae350-linux` 中出现 Hub 超时和 U 盘反复复位，
未完成只读数据检查，不能将控制传输通过视为完整存储验收。

`board linux --usb-readonly` 使用新增的 `usb-readonly` 用户态命令，以只读方式
打开 `/dev/sda`，读取首 1 MiB 两次并比较校验值，然后记录中断和内核日志。
此选项不执行 `--devices` 原有的 USB 尾部改写/恢复测试，也不写 SD 或 Flash。
Linux L1D 开启时的 DMA 一致性与存储错误原因仍待进一步验证；硬核 MMSC_CFG
读回 `0x2007f039`，未置 PPMA 位，PMA CSR 访问返回错误，不假设动态非缓存区域可用。

裸机 `--define USB_TEST_MSC=1` 可启用附加 MSC 检查，链接同版本 `msc_host.c`，
并加入其头文件目录。它只读取 LBA 0 两次，校验重复 CRC 和前后保护字。
`20261105T055237685298Z-board-ae350-usb-suite` 中该单项通过，CRC 均为
`2f45b85d`；但 HID 没有完成挂载，综合套件仍判失败，不作为多类设备并发验收。
默认诊断保留独立 HID 枚举回归；附加 MSC 模式不会放宽 HID 或读取检查。

同一 `0c2366135c33` 的后续重复测试未稳定通过，重插外接 Hub 后也未恢复。
`20261105T143319478906Z-board-ae350-usb-suite` 的 HID＋MSC 组合失败；
不能将较早的一次成功枚举视为重复性验收通过。该位流的 DDR 复核
`20261105T143103515901Z-board-ae350-ddr-suite` 通过 13 项、0 失败，
SD 只读一致性复核 `20261105T144032824622Z-board-ae350-sd-suite` 通过；
后者没有独立主机扇区 CRC，仍不宣称主机参考校验通过。

诊断可通过 `--define CFG_TUSB_DEBUG=2` 启用 TinyUSB 阶段日志，直接输出 UART，
不依赖 BIOS syscall 或 Newlib。载荷 `506543715ebb` 的阶段日志见
`20261105T143630020998Z-board-ae350-usb-suite`：读到 Hub `05e3:0606`，
但 25 字节配置描述符多次在收到 24 字节后失败；该轮最终仍未完成枚举。
随后 JTAG 观察到 Hub 控制 TD 的 CC=2（bit-stuffing error），见
`20261105T143839751901Z-board-ae350-jtag`，不能直接据此认定电气故障或 PHY 根因。
`ohci_phy_test.py` 使用锁定的真实 PHY RTL 检查 0–64 字节短包、填充位、
采样相位、±2000 ppm、边沿抖动和两线 15 ns 偏移，数字仿真通过；
此结果不替代实板波形和重复枚举验收。

绕过外接 Hub，将 Logitech 接收器直连 Soft USB 后，标准非 debug 载荷
`6e7a8e81aa5c` 连续两轮 SRAM 重配置后的枚举通过，回执为
`20261105T144450847241Z-board-ae350-usb-suite` 和
`20261105T144648068784Z-board-ae350-usb-suite`：`046d:c52b`、3 个 HID 接口，
各观察 60 秒且无脱离。第二轮用户确认已输入，同时 UART 收到 2 条报告
（`00000001`、`00490000`）。这是直连枚举与实际报告的证据，不延伸为 Hub、
MSC 或 Linux 验收；套件自身不推断用户动作，其 `physical_keypress_verified`
字段仍保留自动检查的 `false`，上述操作相关性另记于此。

LED 修正后的候选 `67c2ddca7e04` 同样通过标准直连枚举，回执
`20261105T145439929159Z-board-ae350-usb-suite`：`046d:c52b`、3 个 HID 接口，
60 秒无脱离。本轮未另外操作键鼠，报告数为 0，不挪用旧位流的输入结果。

同一新位流恢复 Hub＋U 盘后的组合测试
`20261105T145809806445Z-board-ae350-usb-suite` 未通过，CPU 停在 MSC 命令包完成
断言。debug 载荷 `8895e4035a20` 的回执
`20261105T150756887874Z-board-ae350-usb-suite` 显示 Hub 与 U 盘均已配置，
首个 Test Unit Ready 的 31 字节命令包成功，但随后 13 字节状态包失败，
后续 Read Capacity 命令包也失败并触发 `msch_xfer_cb` 第 336 行断言。
`20261105T151047715566Z-board-ae350-jtag` 观察到相关 TD 的 CC=5
（device not responding）；`20261105T151236111349Z-board-ae350-jtag` 核对裸机
`mcachectl=0`。与旧位流的 Hub 配置/填充位错误分别记录，不认定为同一根因。

可选 `--define USB_ORDERED_MMIO=1` 在 OHCI MMIO 前用 fence 和 DDR 只读响应
排空 CPU AHB 适配器的 posted-write 缓冲；默认关闭。对照载荷 `784825fac348`
及 `20261105T150354717233Z-board-ae350-usb-suite` 未消除失败，因此它不是已验证
修复，也不提供 Linux 缓存一致性。`ohci_phy_tx_test.py` 检查真实两级输出寄存器
后的 NRZI、填充位与 2-bit SE0＋1-bit 驱动 J，包含 31 字节负载，仿真通过。
`ohci_in_transfer_test.py` 的真实 OHCI＋DDR 模型完成 9/13/25 字节多包 IN，
校验 toggle、CRC、字节掩码、缓冲区保护和 TD 收尾；仍不替代存储实板验收。
上述 RX、TX、多包 IN 和 USB 结果合同回归的合并记录为
`20261105T152335805896Z-ae350-ohci-protocol-regressions-a88cb0`，全部通过。

新位流的 Linux 只读复核 `20261105T152150496407Z-board-ae350-linux`
通过用户态自测，标准 OHCI 绑定并识别 Hub 与 YubiKey HID，但 Hub 超时、
U 盘反复复位，`/dev/sda` 不可用，USB 只读测试仍失败。这一结果不签收
Linux 存储或 DMA 缓存一致性。过程只用 SRAM、UART 和 TFTP，不写 Flash、SD 或 U 盘。

关闭 MSC 的 Hub＋HID 复核 `20261105T152334816192Z-board-ae350-usb-suite`
通过：`090c:1000` 设备身份、`1050:0407` 的两个 HID 接口，60 秒无脱离。
该模式不执行存储读写，未看到 Hub 下 Logitech 的接口，不签收所有 Hub 端口。
因此 OHCI 仍为实验候选，不能据此宣称已完整替代此前的 PIO 存储功能。

## PIO 后端历史记录

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

修正后 gateware `build/runs/20261102T194334412350Z-build-usb-eop-driven-j-bf4108/result.json`（`7522f3e526c7`，setup/hold 0/0，最小脉宽余量 0.1ns，image ABI 与旧版相同，沿用已安装 BIOS）以相同内核、U-Boot 和载荷连跑 3 次 `board linux --devices`（`20261102T200546620663Z`、`20261102T200718150537Z`、`20261102T200859479060Z`）：重发 0 次、无 -71，YubiKey、Logitech 接收器与 U 盘每次枚举，U 盘读 567–584 KiB/s、写 275–277 KiB/s，末尾改写/回读/恢复一致。丢握手问题按此关闭；此后的位流 `24a6adbf…`（mem 并入 DDR UI）在 `20261103T043635177186Z` 同样 0 重发。gateware 写入 sysflash 仍需明确授权。共享驱动的包等待与调度改动及 PHY 修正尚未在 20K 实板复验。
