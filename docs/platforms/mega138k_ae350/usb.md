# AE350 Soft USB Host

Dock 唯一 Soft USB 口连接独立 60MHz 全速 PIO Host，APB 控制面保持 sys50，通过 AXI-Lite 异步 FIFO 访问 USB 域。时钟取自机器 `clocks.usb`，PLL 使用 50MHz ×24 ÷20，VCO 1200MHz。固件复用 TinyUSB Host/HID/Hub 和 PIO HCD，诊断程序采用轮询；当前没有 BIOS 键盘输入集成，不宣称 LS、HS 或存储类支持。

## 板级连接

发送 D+/D− 为 M21/M22，保留 42Ω 串联电阻；差分接收比较器为 U21/V21、T20/U20，使用板上参考电压网络。M19 的 Device 上拉、T19/U19 的零欧 termination 支路保持高阻，Host 下拉使用外接电阻。

用户已确认下拉改造完成，最终阻值尚未登记。USB Host 下拉可使用 15kΩ；USB resistor ECN 范围为 14.25–24.8kΩ。此前供电故障由实板 D8 反装引起，用户确认已修复。原理图规定方向是板上 +5V → FB4 → D8 → FU1 → J5 VBUS。

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
