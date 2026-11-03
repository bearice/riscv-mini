# AE350 P8 当前状态与交接（2026-11-03）

P8 的三项（网络、USB、Linux）分别出结果，见 [migration-stages.md](migration-stages.md)。本文只记录当前状态和剩余工作；不是发布，也不是已验收的硬件版本。历史过程见 Git 记录与 `build/runs/` 回执（本机，不随 Git 分发）。

通用约束：重复构建复用 `--workspace default`；板测只写 FPGA SRAM、不改 `current`；Flash 只在用户明确授权时写（已授权并完成：U4 ROM、U7 BIOS、SD 启动文件；gateware 留在 SRAM，USB 稳定后再写 U4 位流区）；主机对端网卡是 `enp4s0`（192.168.1.1/24），不改 `enp2s0`；不要直接运行 `openFPGALoader --detect/--scan-usb` 或 `programmer_cli --scan-cables/--scan`。

| 项 | 状态 |
|---|---|
| 网络 | RJ45 DDR DMA 描述符环与 RX 时序已修好，49 项包测试和长帧压测通过。吞吐、30 分钟负载、断链重连未做。 |
| USB | Host 枚举通过；Linux 原生 HCD 经 Hub 驱动 U 盘读写/恢复、YubiKey 与 HID 接收器。丢握手已由 PHY EOP 修正关闭（见第 3 节）；真实键鼠输入、拔插恢复、重启重复性、Device 方向未做。 |
| Linux | 6.12.112 Sv32 单核到 `linux-mini>`，原生 ttyS0（PLIC 源 9 中断驱动）、F/D 与 FP 上下文、用户态自测、USB 存储（`--devices`）全部通过。其余外设驱动未启用。 |
| 持久启动 | U4 ROM → U7 BIOS → SD `fw_payload.bin` → OpenSBI → U-Boot 2026.07（SD `fatload` / TFTP）→ Linux，无 UART 输入自动完成。 |

## 1. 当前已验证组合

当前位流 `24a6adbf…` 上：自动启动 `20261103T043715125462Z-board-ae350-autoboot`（PASS：ROM `2ea0b2eda141` → U7 BIOS → SD → OpenSBI → U-Boot → Linux 用户态，Image 读 4.6 MiB/s）；`board linux --devices` `20261103T043635177186Z`（U-Boot TFTP 链，USB 0 重发，存储读写恢复通过）；ddr-suite `20261103T043433887777Z`（10/10）；dma-suite `20261103T044219538249Z`。SD 安装：`20261102T191120089044Z-board-ae350-linux`（前一位流）。U-Boot TFTP 首次验证：`20261102T184015359623Z-board-ae350-linux`（较早的 U-Boot 构建 `1d4e6a6e…`，网卡驱动此后未改）。USB EOP 修正的 A/B 见第 3 节（位流 `7522f3e5…`）。

| 组件 | 准确身份 |
|---|---|
| gateware（SRAM） | `build/runs/20261103T041525542162Z-build-mem-on-ddr-ui-clock-hdmi-lane0-site-f0262d/result.json`（digest `24a6adbf…`，mem 域并入 DDR UI 时钟、USB PHY EOP 驱动 J；SFP+ 未绑定；setup/hold 0/0，最小脉宽余量 0.173ns；PnR 通过，非板级合格版本；image ABI 1350241324 与 `7522f3e5…`、`271e113c…` 相同）。合并的取舍与 A/B 见 [sys50 记录后续](../../../reports/ae350-sys50-simplification.md) |
| ROM bootloader（U4） | 安装回执 `build/runs/20261102T182857671261Z-board-ae350-bootrom/result.json`，build `2ea0b2eda141`。套件需显式 `--bootrom-install` 该回执 |
| BIOS（U7 启动槽） | `build/runs/20261102T183632903771Z-build-bios-uboot-chain-ea50f5/result.json`（payload `22aee5b0a1d3`，`boot sd` 只装 fw_payload.bin）；安装回执 `20261102T191305726970Z-board-ae350-bios-install`（167152 B，crc `27be722a`） |
| OpenSBI+U-Boot + DTB | `build/runs/20261102T190946148795Z-build-uboot-sd-volume-order-4f8bf7`，store `backend/uboot/6e7b9472…/outputs/{fw_payload.bin,mega138k_ae350.dtb}`；U-Boot `tree_sha256` `ff627189…`，OpenSBI `5413c912…`，均经干净 tarball 重放 |
| OpenSBI fw_jump（TFTP 临时启动） | `build/runs/20261102T122919885919Z-build-usb-softhost-dts-fbe2b0`，store `backend/opensbi/83950ab1…/outputs/{fw_jump.bin,mega138k_ae350.dtb}` |
| Linux | store `backend/linux/2ac3e1b3…/outputs/Image`（USB 包重发告警）；补丁集 10/20/30/85，`tree_sha256` `c2c76b59…` 经干净 tarball 重放核对 |
| SD 卡 | 整盘 FAT32（LBA 0 为 mkdosfs 引导扇区），根目录 `Image`/`fw_payload.bin`/`mega138k_ae350.dtb`（另有旧 `fw_jump.bin`）。MBR 区仍留有一个从扇区 16 起的旧分区表项，指向与整盘卷重叠的旧 FAT32；BIOS（FatFs）与 U-Boot `sdboot` 都用整盘卷，主机 OS 会挂载到旧分区。清理该表项或统一重新格式化需用户决定 |

旧位流 `20261031T021721139602Z…`（ABI 1350241324）早于 USB 修复：其约束把 `soft_usb_rx_*` 当作 LVDS25 且未配 `PULL_MODE=NONE`，总线读成 SE1，任何 USB 设备都无法应答，不能用于 USB 验证。

## 2. 网络

- 实现与边界见 [ethernet.md](ethernet.md)。TX/RX 描述符环 2..256 项，OWN/DONE/ERROR 所有权协议；FCS 由硬件生成和检查，IPv4/ICMP/UDP 校验和由软件处理；BSP 轮询。
- RX 数据 lane 使用 GW5A `IODELAY`，寄存器 0x68（广播）/0x6c（逐 lane）在停收排空后动态调 tap（≤240）。室温实测公共窗口 1850~2775 ps，运行时扫描选定 tap 185（2312.5 ps），不代表全 PVT。`machine.json` 的 `ethernet_rx_delay_ps` 为 2500，与通过测试的位流一致。
- 诊断寄存器：0x48 FCS 错误、0x60/0x64 PHY RX_ER 帧数；首坏帧捕获 RAM 在 0x70~0x7c，坏帧不进入 DDR 环。
- 证据：`20261031T030229111275Z-board-ae350-ethernet-suite`：49 项（ARP、ICMP、UDP、坏 checksum 拒绝）0 重试、0 错误；256 帧 1514 B 长帧收发全部正确。
- BIOS TFTP（`firmware/bsp/mega138k_ae350/network/ethernet_tftp.{c,h}`）为 `board linux` 传输 Image/DTB/fw_jump.bin，逐文件长度与 CRC32 核对。BIOS 在 `--host-ip` 子网内取 .20（主机占用时 .21）并先 `set ip`：板子默认的 169.254.20.20 依赖 dhcpcd 的 IPv4LL 路由，该路由在每次 FPGA 重配置掉载波后约 10 秒才恢复，会造成首次 TFTP 超时。
- SFP+ 不属于当前 RJ45 验收。SFP+ 子系统现由 `machine.json` 的 `sfp0`/`sfp1` 设备绑定决定是否生成，默认不绑定：带 SFP+ 的当前源码 PnR 在 `u_sfp_10g_mac/xgmac` TX 路径时序失败（setup 1486、hold 41 条，最差约 −2.6ns），见 [ethernet.md](ethernet.md)。

## 3. USB

Host 枚举已通过（提交 `d491cd5`）：接收为单端 LVCMOS33，9 个 USB 引脚在 `board.json` 配 `PULL_MODE=NONE`，D8 反装已修。实板 `RAW=0x00000009`（J 状态稳定），枚举 Logitech Unifying 接收器（`046d:c52b`），挂载 3 个 HID 接口，打印 `USB ENUM PASS`。电气细节见 [usb.md](usb.md)。pull/term 的 OE=0 表示 Hi-Z，不要改成输出高电平。

Linux 原生驱动已通过实板（`20261102T162127780295Z`）：拓扑为根口 → Genesys 05e3:0606 Hub（`1-1`）→ U 盘 090c:1000（`1-1.1`，3864 MiB）、YubiKey（`1-1.2`）、Logitech 接收器（`1-1.4`）。U 盘顺序读 496–596 KiB/s，写 278 KiB/s（O_DIRECT|O_DSYNC），末尾 256 KiB 保存→写入→回读→恢复一致，FAT 只读挂载可列目录。

丢握手：旧 PHY 在 EOP 的 SE0→J 即释放总线，J 只靠上拉恢复，YubiKey 偶尔收不到主机包（3 次基线共 21 次重发，全部是 SET_ADDRESS 后发往新地址的 SETUP，一次 -71）。`usb_fs_phy` 现驱动 1 bit J 后再释放，修正后 3 次 0 重发。驱动每次重发记 `RX_STAT` 告警。证据与细节见 [usb.md](usb.md#linux-驱动)。

## 4. Linux

### 启动链与地址

- 持久链：U4 ROM → U7 BIOS → `boot sd` 装 `fw_payload.bin`（os.sbi+os.uboot）→ OpenSBI FW_PAYLOAD（控制 FDT 在 os.firmware-fdt）→ S 模式 U-Boot `run sdboot`（与 FatFs 同样优先整盘 FAT）→ `booti`。临时链：BIOS（UART 装载）→ TFTP 装载 fw_jump 或 fw_payload → `boot opensbi`，见 [operations](operations.md#ae350-linux-临时启动)。BIOS 交接前停以太网 DMA、关中断、写回并关闭 I/D cache；OpenSBI 冷启动重新开启 L1 cache 并置 `CCTL_SUEN`，U-Boot 在 S 模式用 `ucctlcommand` 维护 DMA 缓冲。
- U-Boot SD 驱动每条命令最多 8 块：LiteSDCard core 以 `bounded=True` 构建，block count 寄存器只有 4 位。
- 装载地址全部来自地址合同 `address_map.py`（os.kernel 0x00400000、os.sbi 0x01000000、os.uboot 0x01100000、os.dtb 0x03200000、os.firmware-fdt 0x03210000）；BIOS `tftp` 只接受合同区间，`board linux` 核对 OpenSBI banner 的 Firmware Base/Size 与 Next Address/Arg1。DDR 布局、诊断载荷区间（diag.*）与检查规则见[地址合同第 7 节](address-map.md)；`ae350_software_layout_test` 拒绝诊断/示例源码中的字面指针常量和重复外设基址。
- 上游输入可复现：每个 vendor 树由各自的 `firmware/<linux|opensbi|uboot>/install.py --machine mega138k_ae350` 安装（公用函数在 `firmware/vendor_install.py`，规范见 [component-builds](../../common/component-builds.md)），`source.json` 不列补丁；改任一安装输入后需重放 tarball 并更新 `tree_sha256`。`build/vendor` 与 `build/work` 均可重建，构建统一用 `mini.py build linux|opensbi|uboot --machine mega138k_ae350`。

### 已确认的硬件结论（决定当前移植方式）

- **ILM/DLM 按虚拟地址匹配且不能关闭或移动**（milmb/mdlmb 只读 0xa0000001/0xa0200001）。PLIC 曾被 ioremap 到 0xa0000000，写入全部落进本地存储器。Linux `10-reserved-kernel-va.patch` 按合同 `OS_RESERVED_VA`（0xa0000000+0x210000）在 vmalloc 中永久占用该窗口；JTAG 证据 `20261102T103316896730Z-board-ae350-jtag`。
- **DDR 上的 AMO/LR/SC 只在目标行已被读分配进 L1D 时完成**；否则 CPU 发出总线原子访问，硬核 DDR 端口没有独占/锁定信号，AMO 无限重放、SC 永远失败。DLM 上 A 完整可用。无可编程 PMA。用户已确认按硬件限制接受：Linux（`30-no-ddr-atomics.patch`，UP/关中断原子）与 OpenSBI（`20-single-hart-no-atomics.patch`，构建拒绝任何 AMO/LR/SC）长期不用 A；BIOS/载荷与 initramfs 用户态以 `rv32im` 构建，依赖 A 的 libc 程序在 DDR 上会挂住。

  | 情形（`firmware/examples/mega138k_ae350/atomic_probe.c`） | 结果 | DDR 端口观察 |
  |---|---|---|
  | DLM | 通过（AMOADD 100→107，LR/SC 成功→109） | — |
  | DDR 缓存，冷行 / 先存 | AMO 活锁 | 单拍读重复约 1.9 万次/秒，从不写 |
  | DDR 缓存，先读（或先存再读） | 通过 | AMO 不产生总线传输 |
  | DDR 缓存，冷行 LR/SC | SC 16 次重试全部失败 | LR 不分配该行 |
  | DDR 不缓存 | AMO 活锁 | 单拍读后无写 |

  回执均在 `build/runs/20261102T1049…`–`1059…`（构建 `…t1-p0..p4`/`…target-2`、上传、JTAG 各一组），DLM 为 `20261102T092411611199Z-board-ae350-jtag`。独立旁证：[mlapaj/linux_tang_primer_138k](https://github.com/mlapaj/linux_tang_primer_138k)。
- **F/D 可用**：M 模式执行（`fpu_probe.c`，IEEE 结果、fcsr、32 个 F 寄存器；JTAG `20261102T110854375058Z`）与 Linux 上下文保存/恢复（`CONFIG_FPU=y`、`CONFIG_RISCV_ISA_V` 关；init 的 FP-context fork/yield/sleep 测试）均通过。内核 C 为 `rv32im`，FP 指令只在 `__fstate_save/restore`。C 扩展不要求启用。
- **定址 `sfence.vma` 在 A25 上失效**：去掉 `85-tlbflush-full.patch` 后 `/init` 执行失败（-EINVAL，panic，`20261102T112419022654Z-board-ae350-linux`），因此保留全量刷新；根因未查明，性能影响未测。

### 设备树

`firmware/dts/mega138k_ae350.dts` 经 C 预处理使用合同宏，随 OpenSBI 构建。PLIC 为 `andestech,nceplic100`，`#interrupt-cells = <2>`。UART 启用（源 9，`console=ttyS0`）。`usb0`（`riscv-mini,ae350-softusb`，轮询，无中断）启用。`ethernet0`、`mmc0`、`video0`、`audio0`、`board_io0` 为 `disabled`；中断号由 EXT_INT[n] → PLIC 源 n+10 推出（board_io=17、audio=12、video=18、ethernet=19），启用时逐个实板确认；`mmc0` 无 fabric 中断。

## 5. 板子操作

- 串口用 by-id：BIOS UART 为 `/dev/serial/by-id/usb-SIPEED_USB_Debugger_2025030317-if01-port0`；FPGA 下载口 cable serial `2025030317`；CPU JTAG 为 `serial:FactoryAIOT ProA`。JTAG/D2XX 与 `board linux`（TFTP 绑定 69 端口）需 `nix develop -c sudo -n …`。`CPUJTAG` 按 D2XX 序列号在 sysfs 找到接口，打开前自动解绑 `ftdi_sio`、关闭后重绑，无需手工处理。
- 恢复 BIOS 后启动 Linux（U-Boot 链；`--uboot-load net` 改为 TFTP，`--sd-install` 先替换 SD 文件，`--uboot-command` 在 U-Boot 提示符排查；fw_jump 链把 `--os-image` 换成 `$S/fw_jump.bin`）：

```sh
G=build/runs/20261103T041525542162Z-build-mem-on-ddr-ui-clock-hdmi-lane0-site-f0262d/result.json
B=build/runs/20261102T183632903771Z-build-bios-uboot-chain-ea50f5/result.json
P=/dev/serial/by-id/usb-SIPEED_USB_Debugger_2025030317-if01-port0
U=build/store/backend/uboot/6e7b9472137def61817480f71321dbffc952efd0e4fd4c29f641d4af48cdffae/outputs
S=build/store/backend/opensbi/83950ab144ca3ce7502d2b0a9993a67d28a02ce6d03d69702bbeded43b840278/outputs
K=build/store/backend/linux/2ac3e1b34513401ab3b69e8b6081f106b35d0ea5217d628bb06b50c6269ee407/outputs/Image
nix develop -c sudo -n .venv/bin/python scripts/mini.py board ae350-upload --port $P --cable-serial 2025030317 --build $G --payload $B
nix develop -c sudo -n .venv/bin/python scripts/mini.py board linux --machine mega138k_ae350 --build $G --port $P \
  --kernel $K --dtb $U/mega138k_ae350.dtb --os-image $U/fw_payload.bin --host-ip 192.168.1.1 --timeout 300 --devices
nix develop -c sudo -n .venv/bin/python scripts/mini.py board verify --machine mega138k_ae350 --suite autoboot --build $G \
  --port $P --cable-serial 2025030317 --sd-install-result build/runs/20261102T191120089044Z-board-ae350-linux/result.json
```

`--devices` 在 shell 中执行 `usb`（要求 `/dev/sda`，会改写并恢复其最后 256 KiB）与 `dmesg`；没有 U 盘时不要加。

- 诊断套件（`ddr-suite`/`dma-suite`/`io-suite` 等）加 `--cable-serial 2025030317 --bootrom-install build/runs/20261102T182857671261Z-board-ae350-bootrom/result.json --cpu-jtag-location 'serial:FactoryAIOT ProA'`。当前位流上通过：`ddr-suite` `20261103T043433887777Z`（默认参数，10/10；载荷 `20261102T120027582830Z-build-diag-contract-memory…`）、`dma-suite` `20261103T044219538249Z`（载荷 `20261102T120033748741Z-build-diag-contract-dma…`）。`io-suite` 最近一次 `20261102T120739200732Z` 在较早位流上。
- 偶发：CPU JTAG 首次读可能 D2XX 短读（`Short D2XX JTAG read: 0/41`，发生在上传前），重试即可；根因未查。
- BIOS `crc32` 输出 `==> <hex>\r\n> `，提示符匹配用 `\n> `。

## 6. 剩余任务

1. **Linux 外设**：USB 已完成。其余按 board_io → 网络 → HDMI fb → MMC → audio 顺序启用 DTS 节点，逐个确认 PLIC 源号，验证功能与内核 DMA API。网络驱动 `riscv_mini_net.c` 假定 RMII/RTL8201F，AE350 为 RTL8211 千兆，需 `riscv-mini,csr-offsets = <0x80 0x8c 0x88 0x90 0xa0 0x94 0x9c 0x98 0x84 0xac 0x3c 0x38 0xa4 0xa8>` 等适配；`riscv_mini_fb.c` 是 20K 480×272 LCD 驱动，HDMI 需独立 fb 驱动。
2. **Linux 稳定性**：用户态内存/文件压力 30 分钟无 oops。
3. **定址 sfence.vma 根因**：查明 A25 上 85 补丁为何必需，评估全量刷新的性能代价。
4. **网络验收**：千兆协商/MDIO ID/时钟的正式记录、PIO 对照、包长与随机内容覆盖、环回收、断链重连、30 分钟负载无内存损坏、吞吐/CPU 占用/丢包、中断路径实板验收。
5. **USB 验收**：PHY EOP 修正与共享驱动的包等待改动未在 20K 复验（20K 同用 `usb_fs_phy`）。真实键鼠输入、拔插恢复、重启重复性；LS/HS 按目标另验；BIOS 键盘集成；Device 方向 SoftPHY。USB host 诊断载荷没有记录的构建配方（需 TinyUSB 源码与头文件目录），应补到 [usb.md](usb.md) 后编译。
6. **诊断载荷复验**：地址收进 diag.* 后，video、hdmi_tty、audio、sd_benchmark、ethernet/packet、sfp_dual、sfp10g_dma 已构建通过但未上板。
7. **新位流**：gateware `24a6adbf…`（无 SFP+）在 SRAM 中通过自动启动、`--devices`、`ddr-suite`、`dma-suite`；`io-suite`/`ethernet-suite`/视频/音频尚未在该位流上复跑。DDR 时钟不可用时访问 DDR 可能挂住，用户决定不做兜底。写 U4 位流区（`board update`）等用户明确授权。SFP+ 重新启用前需先修 XGMAC TX 时序。
8. **过期构建测试**：`machine_build_test` 报 “Missing declared AE350 input: scripts/modular/...”，在 `14bb35d` 上同样失败，未处理。
9. **过期测试**：`tests/mega138k_ae350/ae350_memory_test.py` 有 10 个错误，调用了已删除的接口（`ae350_image.abi_tag`、模块级 `address_map.DDR_BASE`/`image_abi`），需按现有接口更新。
10. **SD 卡布局**：MBR 区的旧分区表项与整盘 FAT32 重叠（见第 1 节），是否清除或重新格式化由用户决定。
11. **U-Boot**：`fdt_high=~0` 触发上游警告（os.dtb 已对齐，启动不受影响），可改为不设该变量后重放；20K 的 OpenSBI/U-Boot 构建尚未迁到 per-tree install.py 规范。
