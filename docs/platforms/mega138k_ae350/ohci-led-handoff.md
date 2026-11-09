# AE350 活动灯与 OHCI 实验交接（2026-11-06）

本文承接 SOM DONE/READY 活动灯、DMA 握手修复及 OHCI 替换 PIO 的实验。结论是：**活动灯已完成实物验收，当前候选的 SD/DMA/DDR 回归通过；OHCI 存储仍失败，不能完整替代原 PIO 功能。**

代码快照为 main 上的 `ce0db9a15a7a6ac8c9fbfb7045a61f306319482a`，标题为 `snapshot(ae350): preserve OHCI bring-up and validated activity LEDs`，未签名。提交后工作区干净；本文及交接入口更新在该快照之后编写。上板候选来自提交前的开发构建，不宣称已经从干净提交重新构建或正式发布。

本机原始证据在仓库根目录的 `build/runs/<run-id>/result.json` 及同目录日志中，不随 Git 分发。文中 run-id 使用 UTC 时间；本文日期为 Asia/Tokyo。

## 当前结论与板上状态

| 项目 | 当前结论 |
| --- | --- |
| SOM LED2 / DONE / W10 | UART2 RX/TX 活动灯，空闲灭、活动闪，用户确认正常 |
| SOM LED1 / READY / V11 | 原生 SD 活动灯，空闲灭、访问闪，用户确认正常 |
| 原 DMA 握手错误 | ACK 计数与 SD 四相握手已修复，最新候选 DMA suite 通过 |
| 布局布线 | 候选 `67c2ddca7e04` full PnR 通过，setup/hold 违规均为 0，最小脉宽余量 0.216 ns |
| DDR | 同一候选 13 项检查、0 失败；本轮参数为 flags=15、traversal-mb=1、stress-seconds=2，不是全容量遍历验收 |
| SD | 同一候选 25 MHz、4-bit，只读 9 个扇区；重复 CRC 一致，TX FIFO/请求门/排空检查通过；没有独立主机 CRC |
| OHCI 直连 | Logitech `046d:c52b`、3 个 HID 接口通过；旧 OHCI 候选还捕获过用户操作对应的 2 条真实报告，见下文镜像区分 |
| OHCI 经 Hub | 关闭 MSC 的诊断通过 `090c:1000` 身份及 YubiKey `1050:0407` 的 2 个 HID 接口；未看到 Hub 下 Logitech，不签收所有端口 |
| OHCI 存储 | 2026-11-06 复测：裸机 MSC 只读 2/3 通过（一次 SRAM 重载后 banner 瞬时缺失，重跑即过），Linux 只读 1/1 通过，`/dev/sda` 可用。交接时的 CSW/CC 失败未复现，判断为间歇性故障，根因未定位 |
| Linux | 2026-11-06 复测进入用户态并通过用户态自测；OHCI 绑定、Hub/YubiKey/Logitech 枚举，`sda` 附着，首 1 MiB 双读校验通过（约 1.3 MB/s） |
| 其他外设 | 不把历史网络、音频、视频或自动启动验收自动移用到本候选；视频流水修改有 RTL 回归，本轮没有实物视频采集验收 |

最后一次板端操作是 `20261105T152525997814Z-board-ae350-upload`：将 `67c2ddca7e04` 装入 FPGA SRAM，UART 装入 BIOS `f4d7b94bae53`，结果 `uploaded-executed`、CPU running。不是停在故障诊断的 trap 循环。

本轮没有写 U4/U7 Flash、SD 或 U 盘，没有更新 `current`/baseline。持久启动中的旧镜像和 SD 文件没有随 OHCI 实验升级；重新上电后的配置不能当成本候选。

## 接线与主机环境

目标机器为 `mega138k_ae350`，板卡为 `mega138k_pro`。配置为 sys/APB 50 MHz、mem/DDR UI 100 MHz、DDR CK 400 MHz、USB 48 MHz；CPU 配置值 800 MHz，不据此宣称吞吐频率已经独立标定。

| FT4232 通道 | 用途与选择器 |
| --- | --- |
| A | FPGA JTAG；openFPGALoader cable `ft4232`，设备 serial `FT5AW9FX`，channel `0`，频率 `2000000` Hz |
| B | SoC JTAG：`serial:FT5AW9FXB` |
| C | UART：`/dev/serial/by-id/usb-FTDI_OpenRACE_Debugger_FT5AW9FX-if02-port0`，115200 baud |
| D | 辅助 UART/调制解调信号；本轮不使用，RTL 未启用该辅助 UART |

C 口实际接 SOM debug 接口：J5 pin 7（P15，FPGA TX）→ FTDI RX；J5 pin 6（N16，FPGA RX）← FTDI TX；J5 pin 8 共地。此前“GND 后连续 8 个偶数 pin 为 TX/RX/RTS/CTS/DTR/DSR/DCD/RI”的描述属于 **D 口**，不要混为 C 口 UART。

`.tools.local.json` 为本机工具发现配置，当前选用 `fpga_programmer=openfpgaloader`、`fpga_cable=ft4232`、`fpga_cable_serial=FT5AW9FX`、`fpga_channel=0`、`fpga_frequency=2000000`。官方 Gowin programmer 路径仍保留；新 FT4232 的本轮 SRAM 下载实际走 openFPGALoader。使用 `mini.py board`，不手工组合下载器和串口探针。

Linux/NixOS 构建使用 `nix develop`；板端已验证入口为 `sudo -n .venv/bin/python scripts/mini.py ...`。主机对端网卡是 `enp4s0`，最后测试时自动取得 `169.254.52.166/16`。重配置后先等 BIOS 初始化 PHY、链路和 IPv4LL 恢复，再核对 `ip -4 -brief addr show enp4s0`；不要直接照搬旧交接的 `192.168.1.1`，也不要因短暂 NO-CARRIER 就认定网线断开。

Soft USB 使用外接 Host 下拉。`M19/T19/U19/V21/U20` 维持高阻，不能把零欧 termination 支路驱动为低来代替下拉。接收端是 U21/T20 的单端 LVCMOS33，相关 USB 引脚显式 `PULL_MODE=NONE`。最后测试拓扑已由用户恢复为外接 Hub 加原 U 盘、YubiKey、Logitech 接收器；详见 [USB 板级连接](usb.md#板级连接)。

## 镜像与启动固件身份

| 用途 | 身份或构建回执 |
| --- | --- |
| 最新 LED/OHCI gateware | `67c2ddca7e04513159bcaf53d865baef734d4c289d38a0a27d3f3746f54a0883` |
| 最新 gateware 构建 | `20261105T143041301065Z-build-ohci-led-bram-fifo-verification-retry-fca646`；full PnR，image ABI `0x3b63b7a8` |
| 较早 OHCI 对照 | `0c2366135c33992a757159d033b925717d17da34beda494389af58a4b609e22c`；构建 `20261104T173117821400Z-build-ohci-dma-lane-width-fix-4de8ed` |
| 历史 PIO 对照 | `24a6adbfdf2e58bec97ccce22fa2249a0c69baedfed8a7510152064bb3c0f0ba`；构建 `20261103T041525542162Z-build-mem-on-ddr-ui-clock-hdmi-lane0-site-f0262d`；ABI 不同，不混装 OHCI 载荷/DTB |
| 实际安装的 U4 bootloader | `af8501eb06a379e300a867fc3a8d43a6345b6a901cfc22f3196e6af47197f398`，banner `2ea0b2eda141`，4168 B |
| 兼容 bootloader 安装回执 | `20261102T182857671261Z-board-ae350-bootrom`；最新 SD/DDR suite 都再次完整读回匹配 |
| 当前 RAM BIOS | payload `f4d7b94bae537da07ba39d9c839164b354ad01564dc3d6fcba449849284ae556`；构建 `20261104T174549458028Z-build-ohci-linux-loader-5b2e1f` |
| Linux 只读诊断 | `build/store/backend/linux/37a84a22f9fcbe03710fb31d78757c884a366205dae96d25f51eee1da3a7e2d2/outputs/Image` |
| 配套 OpenSBI / DTB | `build/store/backend/opensbi/b01d0ccfa375dbd3a43f9c14c1fbccaa22a09f34f6bde2c78d89005488eb8916/outputs/{fw_jump.bin,mega138k_ae350.dtb}` |

候选依赖的 bootloader 与实际 Flash 中安装的兼容版本不是同一身份。普通 `board run` 回执中的 `rom_build_id` 可能是所选构建的身份字段，不能代替实物读回。套件必须传上述成功的 `--bootrom-install`，使用 `installed-bootloader.readback_matched=true` 的结果；不必为测试重写 Flash。

常用载荷都位于 `build/runs/<下列构建 run-id>/result.json`：

| 载荷 | 构建 run-id | payload digest 前缀 |
| --- | --- | --- |
| SD | `20261105T040555351785Z-build-ohci-led-sd-activity-validation-c1f0f6` | `812fc1256fe3` |
| DMA | `20261105T043039096332Z-build-ohci-registered-bus-dma-validation-e4be08` | `326800c4f033` |
| DDR | `20261105T043116379665Z-build-ohci-registered-bus-ddr-validation-a306a7` | `97afe18fa3d2` |
| 标准 HID | `20261105T102534222387Z-build-ohci-hid-regression-profile-e6add4` | `6e7a8e81aa5c` |
| HID＋MSC 只读 | `20261105T102542508560Z-build-ohci-msc-regression-profile-375176` | `6a6bb5b952ce` |
| MSC 阶段日志 | `20261105T150431619206Z-build-ohci-msc-command-failure-trace-42494f` | `8895e4035a20` |
| DDR 发布顺序对照 | `20261105T150335084645Z-build-ohci-posted-ddr-write-readback-control-4208c4` | `784825fac348` |

## 实现与验证证据

DONE/READY 通过 Gowin `use_done_as_gpio` / `use_ready_as_gpio` 释放，保持低有效。旧低/高阻驱动出现两灯空闲常亮；当前保留 IOBUF，OE 恒为 1，明确输出空闲高、活动低。活动亮 40 ms、强制灭 40 ms，持续传输时闪烁；LED3 是独立电源灯。

OHCI 复用锁定的 `UsbOhciWishbone_Dw32_Pc1_Pf48000000`，PLL 为 50 MHz ×24 ÷25。控制面走 APB→AXI-Lite CDC→Wishbone，DMA 走 32-bit Wishbone→256-bit DDR 队列，成为四路 DDR 仲裁的第四主端口。中断接 EXT_INT[4] / PLIC source 14；Linux 的虚拟 IRQ 编号不能直接当作物理 PLIC source。

此前 DMA 描述符写错字由生成 Verilog 的位移量截断造成：`lane*32` / `lane*4` 的自定宽表达式被截断。现以显式 `Cat` 拼接构造位移量，真实 OHCI＋DDR RTL 回归复现了旧错误并验证修复。与原先 ACK 计数、SD 四相握手问题分别处理；后两项已保存在前一 snapshot `cb79931`。

原 loopback ACK 修复保留异步捕获用于快速撤销 REQ，计数改用同步后 ACK 的上升沿；SD ACK 则保持到对端观察到 REQ 撤销，避免窄脉冲丢失。最初“mem 时钟改变导致重复采样”的猜测没有被确认为根因，不应重新当作既定结论。

本轮时序收敛还保留了 EXTS 响应寄存、AHB 元数据使能调整、DDR 同时钟 FIFO 输出缓冲及像素 FIFO 后的弹性缓冲。FIFO 逻辑容量不变，首字增加一个时钟延迟。

| 最新候选的检查 | run-id / 结论 |
| --- | --- |
| SD 只读 | `20261105T145108407197Z-board-ae350-sd-suite`，PASS；LBA0 重复 CRC `e78641e2`，9 sectors |
| DMA | `20261105T145203643725Z-board-ae350-dma-suite`，PASS |
| DDR | `20261105T145317996803Z-board-ae350-ddr-suite`，PASS，13/0 |
| 直连 Logitech | `20261105T145439929159Z-board-ae350-usb-suite`，PASS，3 HID、60 s 无脱离；该轮 reports=0 |
| Hub＋HID，MSC 关闭 | `20261105T152334816192Z-board-ae350-usb-suite`，PASS，2 HID；不能冒充 MSC 数据测试 |
| Hub＋MSC | `20261105T145809806445Z-board-ae350-usb-suite`，FAIL / 超时后发现 MSC 断言 |
| MSC 阶段日志 | `20261105T150756887874Z-board-ae350-usb-suite`，FAIL，状态包接收失败后断言 |
| MSC 复测（2026-11-06） | `20261105T170546219294Z-board-ae350-usb-suite` PASS（CRC `2f45b85d` 双读一致）；`20261105T170735897717Z-board-ae350-usb-suite` banner 瞬时缺失后重跑 `20261105T170844899575Z-board-ae350-usb-suite` PASS |
| Linux 复测（2026-11-06） | `20261105T171227272914Z-board-ae350-linux` PASS：`sda` 附着，`usb-readonly` 双读 1 MiB checksum `1509237800`，OHCI 中断 686 次 |
| Linux 只读 | `20261105T152150496407Z-board-ae350-linux`，整体 FAIL；其中 `linux_shell=true` |
| 数字协议回归 | `20261105T152335805896Z-ae350-ohci-protocol-regressions-a88cb0`，4 项全 PASS |
| 最后恢复 BIOS | `20261105T152525997814Z-board-ae350-upload`，PASS，uploaded-executed |

真实输入证据属于较早的 `0c2366135c33`：直连 Logitech 两次枚举通过，第二次 `20261105T144648068784Z-board-ae350-usb-suite` 在用户确认操作时捕获 `00000001`、`00490000` 两条报告。新候选 `67c2…` 的直连枚举通过，但该轮未另外操作键鼠。自动回执的 `physical_keypress_verified=false` 没有人为改写；不能把旧候选的物理输入直接算到新候选。

数字回归覆盖真实 PHY 的短包、填充位、采样相位、±2000 ppm、±8 ns 边沿抖动及两线 15 ns 偏移；TX 检查真实两级输出寄存器后的 NRZI、2-bit SE0＋1-bit 驱动 J；真实 OHCI＋异步 DDR 模型检查 9/13/25 字节多包 IN 的 toggle、CRC、数据、字节保护和 TD 收尾。它们不能替代实板存储或物理波形验收。

## 尚未解决的问题与已做对照

最新可复现故障发生在 MSC，不再只是“Hub 无法枚举”：

1. Hub 和 U 盘均已配置。
2. 首个 SCSI Test Unit Ready 的 CBW（31 B OUT）完成成功。
3. CSW（13 B IN）返回失败，完成长度为 0。
4. 随后的 Read Capacity CBW 也失败，TinyUSB `msch_xfer_cb` 在 `msc_host.c:336` 断言；CPU `mcause=3`，不能只把 UART 静默解释为 loader 故障。

`20261105T151047715566Z-board-ae350-jtag` 观察到相关 GTD 的 CC=5（device not responding）、CSW 缓冲为 0。较早 `0c2366…` 则曾在 Hub 的 25 B 配置描述符收到 24 B 后失败，并观察到 CC=2（bit-stuffing）；见 `20261105T143839751901Z-board-ae350-jtag`。不要未经验证把两个现象归为同一根因。

| 已尝试事项 | 结果与限制 |
| --- | --- |
| 外接 Hub 重插 | 旧 OHCI 候选仍失败；不要当作尚未做过的建议反复要求用户操作 |
| 绕过 Hub 直连 Logitech | 旧、新 OHCI 候选枚举均通过；旧候选有真实报告，但不等于 bulk 存储已通过 |
| `USB_ORDERED_MMIO=1` | MMIO 前 fence＋DDR 只读排空 CPU AHB posted-write；`20261105T150354717233Z-board-ae350-usb-suite` 仍失败，默认保持 0，不是已验证修复 |
| 裸机缓存状态 | `20261105T151236111349Z-board-ae350-jtag` 实读 `mcachectl=0`；不能用 Linux L1D 问题解释所有裸机失败 |
| Linux DMA 一致性 | 当前仍为待验收项，配置有 `ARCH_DMA_DEFAULT_COHERENT`；不要据局部枚举判断已一致 |
| Linux 临时关闭 L1D | 运行中关闭未恢复；一次在 kernel entry 前关闭的实验随后发生 `rdtimeh` 非法指令 panic，是无效的缓存对照，不能据此判断根因；已恢复正常启动流程 |
| 动态 PMA | MMSC_CFG `0x2007f039` 未置 PPMA，PMA CSR 访问报错；不假设可随意配置非缓存 DMA 池 |
| PHY 数字模型 | RX/TX 与多包 DMA 模型通过；尚未证明真实 Hub 链路的波形、接收或调度问题已排除 |

实现入口见 [OHCI 与 DDR 适配器](../../../gateware/machines/mega138k_ae350/peripherals/usb_ohci.py)、[裸机诊断](../../../firmware/diagnostics/mega138k_ae350/usb/main.c) 与 [OHCI 平台接缝](../../../firmware/diagnostics/mega138k_ae350/usb/ohci_litex.h)。TinyUSB MSC 为同一锁定版本 0.20.0；不能通过去掉断言或放宽判定将存储失败改成通过。

另一项未完成的优化是 DDR FIFO 的 block RAM 映射。当前构建只记录读取最后的 `ae350_ethernet.gsc`，单独添加的 `ae350_ddr_fifos.gsc` 未生效，相关 FIFO 仍使用 FF。不要声称 BRAM 优化已成功。当前 STA 已通过；若后续确实要处理该优化，应合并约束并重新检查综合结果和 PnR。候选资源为 BSRAM 134/340、FF 39363。

## 2026-11-06 复测与 DTB overlap 修复

2026-11-06（JST）复测同一候选 `67c2…`：裸机 MSC 组合测试两次通过（中间一次 SRAM 重载后 loader banner 瞬时缺失，重跑即恢复），Linux 只读测试通过（`sda` 附着、首 1 MiB 双读校验、约 1.3 MB/s）。交接中记录的“CSW 失败 → Read Capacity 断言、U 盘复位/超时”在本轮未复现，判断原故障为间歇性：可能与物理链路状态、Hub 供电/复位时序或 PHY 裕量有关，尚无波形证据定位根因。若再次出现，应按“总线无回应（CC=5）vs 接收/状态处理错误（CC=2/CSW 长度为 0）”分类记录 GTD 条件码后再改代码。

同轮 Linux 日志出现 `OF: reserved mem: OVERLAP DETECTED!`：DTB 的 `opensbi@1000000`（`0x01000000–0x01100000`）与 OpenSBI 运行时注入的 `mmode_resv*` 保留节点重叠。已删除 DTB 中的 `opensbi@` 节点（`firmware/dts/mega138k_ae350.dts`），OpenSBI 已为其固件与 scratch 区域注入 no-map 保留；`tests/mega138k_ae350/ae350_software_layout_test.py` 同步改为断言 DTB 不含 `opensbi@`，单位地址负例改用 `plmt0` 节点。primer20k 的 OpenSBI 注入逻辑（`scripts/modular/machines/primer20k_vexriscv/opensbi.py`）仍向其 DTB 插入 `opensbi@`，其 Linux 启动若出现同类警告需另行核对，不在本轮结论范围内。

修复验证：`opensbi` 构建缓存键原先不含 DTB 源哈希（只记录不参与键），导致 DTS 改动不触发重编、产物 DTB 仍为旧版；已将 `dtb_source_sha256` 纳入 `scripts/modular/machines/mega138k_ae350/opensbi.py` 的工作区缓存键。新 OpenSBI 产物 `6ff6328f6d9a7c3433c3a12234faacacc4e4f3abc6632c4b3cfb45e0358c5221`（DTB sha256 `d4d34714…`）经 `20261105T171736989700Z-board-ae350-linux` 复核：启动日志无 `OVERLAP DETECTED`，仅剩 OpenSBI 注入的 `mmode_resv*`，Linux 只读存储仍通过（checksum `1509237800` 与上轮一致）。板上已恢复 BIOS `f4d7b94bae53`。

## 接手后的优先工作

活动灯与原 DMA 握手任务已完成，不必重新定位极性。OHCI 后续应围绕上述 bulk 状态包失败保留证据：在同一候选和同一拓扑下，关联实际 IN token、设备响应、GTD condition code、toggle/重试和 DMA 完成；先区分“总线无回应”与“接收/状态处理错误”，再修改 PHY、HCD 或 DMA。Linux 缓存一致性单独验收，不把无效 cache-off 实验当结论。

若修改硬件，使用现有 `default` 工作区串行构建、保留缓存。先跑受影响的 RTL 回归，再检查 setup、hold、最小脉宽，最后在明确的新镜像上复核 SD/DMA/DDR、USB 直连和 Hub＋MSC；不要将旧镜像 PASS 移作新镜像证据。频繁轮询构建进度不是用户偏好，使用完成等待并只报告实质进展。

此前增量 PnR/placement 尝试的匹配率仅约 56%–60%，低于工具门槛后实际回退 full；以回执和 `pnr-flow.json` 的 actual flow 为准，不能只看请求参数。最终 `67c2…` 是 full PnR。中断或只到布线阶段、没有最终 STA 的构建不能作为通过结果。

## 继续操作的命令

在仓库根目录先读 `scripts/mini.py --help`、[操作指南](operations.md)；构建/板测时分别使用仓库的 build/test skill。以下是操作配方，不会在阅读本文时自动执行。

```sh
task_gateware=build/runs/20261105T143041301065Z-build-ohci-led-bram-fifo-verification-retry-fca646/result.json
task_bootrom=build/runs/20261102T182857671261Z-board-ae350-bootrom/result.json
task_uart=/dev/serial/by-id/usb-FTDI_OpenRACE_Debugger_FT5AW9FX-if02-port0
task_cpu_jtag=serial:FT5AW9FXB
task_bios=build/runs/20261104T174549458028Z-build-ohci-linux-loader-5b2e1f/result.json
task_usb_hid=build/runs/20261105T102534222387Z-build-ohci-hid-regression-profile-e6add4/result.json
task_usb_msc=build/runs/20261105T102542508560Z-build-ohci-msc-regression-profile-375176/result.json

# USB 组合只读测试；目前已知失败，按原合同判定。
sudo -n .venv/bin/python scripts/mini.py board usb-suite --machine mega138k_ae350 \
  --build "$task_gateware" --payload "$task_usb_msc" --port "$task_uart" \
  --cpu-jtag-location "$task_cpu_jtag" --cable-serial FT5AW9FX \
  --bootrom-install "$task_bootrom" --timeout 90

# 标准 HID：把上一条 --payload 改为 "$task_usb_hid"。
# 阶段日志：改用表中的 MSC 阶段日志构建回执。

# 诊断断言后读取 CPU 状态；不擅自改写缓存/PMA CSR。
sudo -n .venv/bin/python scripts/mini.py board jtag --machine mega138k_ae350 \
  --cpu-jtag-location "$task_cpu_jtag" --exec 'status; read-reg mcause; read-reg mepc; read-reg 0x7ca'

# 测试结束后恢复 BIOS；只装 SRAM 和 UART 载荷。
sudo -n .venv/bin/python scripts/mini.py board run --machine mega138k_ae350 \
  --build "$task_gateware" --payload "$task_bios" --port "$task_uart" \
  --cpu-jtag-location "$task_cpu_jtag"
```

重新构建 USB 阶段诊断时，保留全部 TinyUSB 编译输入，不要只编 `main.c`：

```sh
nix develop -c python scripts/mini.py build payload --machine mega138k_ae350 \
  --hardware "$task_gateware" --workspace default --source-dir firmware/diagnostics/mega138k_ae350/usb \
  --include-dir firmware/vendor/tinyusb/src --include-dir firmware/vendor/tinyusb/src/host \
  --include-dir firmware/vendor/tinyusb/src/common --include-dir firmware/vendor/tinyusb/src/class/hid \
  --include-dir firmware/vendor/tinyusb/src/class/msc --include-dir firmware/vendor/tinyusb/src/portable/ohci \
  --extra-source firmware/vendor/tinyusb/src/tusb.c --extra-source firmware/vendor/tinyusb/src/common/tusb_fifo.c \
  --extra-source firmware/vendor/tinyusb/src/host/usbh.c --extra-source firmware/vendor/tinyusb/src/host/hub.c \
  --extra-source firmware/vendor/tinyusb/src/class/hid/hid_host.c \
  --extra-source firmware/vendor/tinyusb/src/class/msc/msc_host.c \
  --extra-source firmware/vendor/tinyusb/src/portable/ohci/ohci.c \
  --helper string --define USB_TEST_MSC=1 --define CFG_TUSB_DEBUG=2 --purpose ohci-handoff-trace

# 硬件只在修改后构建；保持同一工作区，不以不同 purpose 新开 workspace。
nix develop -c python scripts/mini.py plan gateware --machine mega138k_ae350
nix develop -c python scripts/mini.py build gateware --machine mega138k_ae350 \
  --workspace default --synthesize --pnr-mode full --purpose ohci-handoff-debug
```

Linux 只读复核需要先恢复 BIOS，并确认主机地址仍在 `enp4s0` 上：

```sh
task_kernel=build/store/backend/linux/37a84a22f9fcbe03710fb31d78757c884a366205dae96d25f51eee1da3a7e2d2/outputs/Image
task_sbi=build/store/backend/opensbi/b01d0ccfa375dbd3a43f9c14c1fbccaa22a09f34f6bde2c78d89005488eb8916/outputs
sudo -n .venv/bin/python scripts/mini.py board linux --machine mega138k_ae350 \
  --build "$task_gateware" --port "$task_uart" --kernel "$task_kernel" \
  --os-image "$task_sbi/fw_jump.bin" --dtb "$task_sbi/mega138k_ae350.dtb" \
  --host-ip 169.254.52.166 --usb-readonly --timeout 180
```

只读排查使用 `--usb-readonly`。`--devices` 含 USB 尾部 256 KiB 改写/恢复，`--sd-install` 写 SD，`board update` 和 `bootrom --install` 写 Flash，均不是本轮交接授权的下一步。

历史 PIO、网络、持久启动及更早硬件限制见 [P8 历史交接](p8-handoff.md)；其旧下载器、旧串口、PIO USB 的通过结果不代表当前 OHCI 候选。普通提交沿用未签名要求；提交 snapshot 不等于 release，不自动更新版本、CHANGELOG、tag 或触发构建/归档。
