# AE350 P8 当前状态与交接（2026-11-02）

P8 的三项（网络、USB、Linux）分别出结果，见 [migration-stages.md](migration-stages.md)。本文只描述当前状态和剩余工作，不是发布，也不是已验收的硬件版本。通用约束：重复构建复用 `--workspace default`；只写 FPGA SRAM，不写 Flash、不改 current；主机对端网卡是 `enp4s0`，不改 `enp2s0`。

| 项 | 状态 |
|---|---|
| 网络 | RJ45 的 DDR DMA 描述符环和 RX 时序已修好，49 项包测试与长帧压测通过。吞吐、30 分钟负载、断链重连未做。 |
| USB | Host 枚举已通过（Logitech 接收器，3 个 HID 接口）。真实键鼠输入、拔插恢复、重启重复性未做；Device 验证未做。 |
| Linux | 内核已能在板上起到驱动 probe，还没有 initramfs shell。设备树刚改完，未上板验证。 |

## 1. 网络

- 实现细节与边界见 [ethernet.md](ethernet.md)。TX/RX 描述符环 2..256 项，OWN/DONE/ERROR 所有权协议；以太网 FCS 由硬件生成和检查，IPv4/ICMP/UDP 校验和由软件处理；BSP 目前轮询。
- RX 时序已解决：RX 数据 lane 使用 GW5A `IODELAY`，寄存器 0x68（广播）和 0x6c（逐 lane）可在停收且排空后动态调 tap（每 lane tap ≤ 240）。实测公共窗口 1850~2775 ps，运行时扫描选定中心 tap 185（2312.5 ps）。室温窗口，不代表全 PVT。注意：通过测试的位流（gateware `ac48ff0b…`）综合时 `ethernet_rx_delay_ps` 为 2500，当前工作区 `machine.json` 也是 2500，而 HEAD 里是 2300，提交前需确认该值。
- 诊断寄存器：0x48 FCS 错误、0x60/0x64 PHY RX_ER 帧数；首坏帧捕获 RAM 在 0x70~0x7c，坏帧不进入 DDR 环。
- 板测证据（本机 `build/runs/`，不随 Git 分发）：
  - `20261031T030229111275Z-board-ae350-ethernet-suite/result.json`：49 项（ARP、ICMP、UDP、坏 checksum 拒绝）0 重试、0 错误；256 帧 1514 B 长帧收发全部正确，0 丢帧、0 CRC/FCS/RX_ER 错误。
  - 当前使用的位流候选：`20261031T021721139602Z-build-ethernet-p8-rx-diag-dyn-iodelay-8c34e8/result.json`。
- 未完成：千兆协商/MDIO ID/时钟检查的正式记录、DMA 之外的 PIO 双向对照、包长与随机内容覆盖、环回收、断链重连、30 分钟负载下无内存损坏、吞吐/CPU 占用/丢包记录、中断处理实板验收。
- SFP+ 未实现，不属于当前 RJ45 验收。

## 2. USB

Host 枚举已通过（提交 `d491cd5`）：接收改为单端 LVCMOS33，9 个 USB 引脚在 `board.json` 配 `PULL_MODE=NONE`，D8 反装已修。实板 `RAW=0x00000009`（J 状态稳定），枚举 Logitech Unifying 接收器（`046d:c52b`），挂载 3 个 HID 接口，打印 `USB ENUM PASS`。证据和电气细节见 [usb.md](usb.md)。pull/term 的 OE=0 表示 Hi-Z，不要改成输出高电平。

未完成：真实键鼠输入（HID 报告数不代替物理按键验收）、断开重连、重启重复性；Hub/LS/HS 按实际目标另验，目前不宣称支持；没有 BIOS 键盘集成。Device 方向的 SoftPHY 单独验证未做。

## 3. Linux

### 已完成

- BIOS 内 TFTP 引擎：`firmware/bsp/mega138k_ae350/network/ethernet_tftp.{c,h}`、`bios/main.c`。Image、DTB、fw_jump.bin 传输后 CRC 一致。
- OpenSBI AE350 平台与 probe：`firmware/opensbi/probe/*`，产物 `build/opensbi-ae350/platform/generic/firmware/fw_jump.bin`。实板进入 S-mode。
- 通用 JTAG 入口 `mini.py board jtag`：`scripts/mini_ops/mega138k_ae350/ae350_jtag.py`，支持 halt/resume/reset/pc/读写寄存器和内存，已实板验证。
- Linux 6.12.112 的 UP 无原子指令改造：硬核 AHB 数据口上的任何 AMO/LR/SC 会令核心死锁（见 `firmware/diagnostics/mega138k_ae350/main.c`），所以按 `CONFIG_SMP=n` 构建，`arch/riscv/Makefile` 去掉 `a`，`asm/{cmpxchg,atomic,bitops,futex}.h` 改用关中断回退，`entry.S` 的 `sc.w` 用 `#ifdef CONFIG_SMP` 包住。vmlinux 中 AMO/LR/SC 为 0。源码在 `/home/bearice/riscv-mini-linux/linux-6.12.112/`（非 git 仓库，只有 tarball），用 `LLVM=1` 构建，产物 `/home/bearice/riscv-mini-linux/obj-minimal/arch/riscv/boot/Image`。
- 启动：OpenSBI v1.9 → Linux，Sv32、PLMT、PLIC、earlycon 正常，到达驱动 probe。

### 设备树（未上板验证）

`firmware/dts/mega138k_ae350.dts` 已改并编译为 `build/mega138k_ae350.dtb`，用新 DTB 的启动还没做。

- `plic0` 改为 `#interrupt-cells = <2>`：`andestech,nceplic100` 带边沿中断 quirk，需要 two-cell，否则串口、board_io 解析中断得到 `-ENXIO`。
- 上次崩溃原因：`mmc0` 的 reg 抄自 20K（`0xf8009000`），AE350 上访问触发 load access fault。
- 为先拿到 shell，`ethernet0`、`mmc0`、`video0`、`audio0`、`board_io0` 全部 `disabled`。基址与 `address_map.py` 一致：`video0=0xf8030000`、`board_io0=0xf8080000`、`audio0=0xf8010000`、`mmc0=0xf8040000`。
- 中断号按 `fabric.py` 的 `cpu.interrupt` 位序推算：EXT_INT[n] 对应 PLIC 源 17+n（实测 EXT_INT[0]=17），即 audio=19、video=25、board_io=24、ethernet=26，均为边沿。这是推算，未逐个在板上确认。`mmc0` 没有接 fabric 中断，DTS 里已去掉 `interrupts`。
- 驱动适配缺口：`riscv_mini_net.c` 假定 RMII/RTL8201F，AE350 是 RTL8211 千兆；启用时需 `riscv-mini,csr-offsets = <0x80 0x8c 0x88 0x90 0xa0 0x94 0x9c 0x98 0x84 0xac 0x3c 0x38 0xa4 0xa8>`。`riscv_mini_fb.c` 是 20K 的 480×272 LCD 驱动，AE350 需要 HDMI 的独立 fb 驱动。

### 剩余工作（按顺序）

1. 启用驱动前在板上确认各节点的 PLIC 源号，非必需节点保持 disabled。
2. 用新 DTB 做 TFTP 启动：Image@0x00400000、DTB@0x01f00000、fw_jump@0x01000000，`go 01000000 01f00000`，到 initramfs 的 `linux-mini>` 提示符。
3. 提供 AE350 版 `mini.py board linux`，参照 `scripts/mini_ops/primer20k_vexriscv/linux.py`（TFTP 服务沿用 `scripts/primer20k_vexriscv/bios_tftp.py`，串口用 by-id 路径），回执写入 `build/runs/`。
4. 逐项启用驱动：board_io、网络、HDMI fb、MMC、audio；每项验证功能和内核 DMA API；用户态内存/文件压力 30 分钟无 oops；F/D/C 在工具链和上下文保存测试后分别开启。

## 4. 板子操作约定

- `ftdi_sio` 重新加载后 `/dev/ttyUSB*` 编号会变。当前：`ttyUSB0/1` 是 `SIPEED USB Debugger 2025030317`（FPGA 下载口，**BIOS UART 是 if01，即 ttyUSB1**）；`ttyUSB2/3` 是 `FactoryAIOT Pro`（CPU JTAG），没有 BIOS 输出。优先用 `/dev/serial/by-id/usb-SIPEED_USB_Debugger_2025030317-if01-port0`。
- 加载位流和 BIOS（已成功，回执 `build/runs/20261102T030051314751Z-board-ae350-upload`）：

```sh
nix develop -c sudo -n .venv/bin/python scripts/mini.py board ae350-upload \
  --port /dev/serial/by-id/usb-SIPEED_USB_Debugger_2025030317-if01-port0 \
  --cable-serial 2025030317 \
  --build build/runs/20261031T021721139602Z-build-ethernet-p8-rx-diag-dyn-iodelay-8c34e8/result.json \
  --payload build/runs/20261102T010709487339Z-build-candidate-8f1121/result.json
```

  不带 `--cable-serial` 时 Gowin programmer 扫不到 cable；默认 `--port` 是 Windows 的 `COM4`。
- JTAG 需要 `sudo -n`（D2XX）。USB 端口异常时可通过 `/sys/bus/usb/devices/usb1/1-0:1.0/usb1-port4/disable` 复位。
- BIOS 的 `crc32` 输出 `==> <hex>\r\n> `，提示符匹配要用 `\n> `，不能用 `> `。

## 5. 提交前注意

- `scripts/test_boot_tftp.py` 与 `scripts/test_ae350_linux_boot.py` 是临时脚本，含硬编码路径、`/dev/ttyUSB3`、`/tmp/tftp_runner.sh`。并入 `mini.py board linux` 后删除，不要直接入库。
- 未逐项复核用途的改动：`gateware/machines/mega138k_ae350/address_map.py`、`machine.json`、`flake.nix`。提交前按 `docs/common/pre-commit-sop.md` 审阅并显式暂存。
- 定位串口时曾直接调用 `openFPGALoader --detect/--scan-usb`、`programmer_cli --scan-cables/--scan`，并用 `board jtag --write-word` 向 UART 写字节。这是一次性诊断，未改 Flash、未形成脚本，但违背"不要自行扫描"的要求，不要重复；端口按上面的约定确定。
