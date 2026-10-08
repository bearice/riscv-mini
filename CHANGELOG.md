# Changelog

版本与功能阶段的对应关系见 [docs/version-history.md](docs/version-history.md)。0.7.1 及之前所有组件共享一个版本号（条目形如 `## v0.7.1`）。从 0.7.2 起版本按组件独立递增，条目形如 `## <组件> v<版本>`（如 `## bios v0.7.2`、`## uboot v0.1.0`），系统包条目形如 `## system v<版本>`；组件版本见 `VERSIONS.yaml`，已验收的组件组合见 `RELEASES.yaml`。每个版本的构建包都由该版本提交自己的 `scripts/build.py` 生成；2026-10-06 按版本顺序逐个上板，用该提交自己的验收脚本做实板测试，并运行该提交自带的基准，现场条件见 [docs/lab-environment.md](docs/lab-environment.md)。v0.7.1 是合并版本时代的最后一次正式发布。

## system v0.8.0 — RAM 零基址与 Flash XIP 启动

- 组合 rtl/bootloader/hal/bios/opensbi 0.8.0、uboot 0.2.0；full RV32IMAF，CPU/sys 60 MHz、DDR 120 MHz、4 KiB shared writeback L2，关闭 C，使用 Flash XIP 启动、片内 ROM 为 0。
- RAM 从 0 开始，设备 CSR/MMIO 位于 0xF0000000 以上，XIP 位于 0xF3000000，复位入口 0xF3100000 对应 Flash 物理 1 MiB；VRAM 由 CSR 指定 DDR 地址。本次布局不增加兼容模式 flag。
- 功能阶段已通过 PnR 0/0、完整 BIOS 板测和 60 秒 soak、三轮 all/cache，及独立 OpenSBI/U-Boot 验收；正式提交的镜像、时序、资源、板测、性能与 SHA256 以发布包 release.json 和 reports 为准。
- OpenSBI/U-Boot 在系统包中仍声明为 external，由独立构建与板测流程发布。FPGA 仅加载 SRAM，BIOS 通过 UART 加载 DDR；未验证持久 gateware 断电冷启动、内核启动、USB 存储及物理输入。

## rtl v0.8.0 — 高地址设备空间与动态帧缓冲

- RAM 零基址，Ethernet/USB/XIP 分别迁移到 0xF1000000/0xF2000000/0xF3000000；CPU 生成器默认匹配新 IO/cache 属性并拒绝旧属性 CPU 输入。
- 添加只读 NOR XIP、上电 ABh 唤醒、软件 SPI 引脚仲裁及 enable/busy CSR；关闭禁止新读取，在途请求正常结束。
- LCD base0/base1 CSR 指定 DDR 帧缓冲；地址与槽选择按帧锁存，校验对齐、零页和完整帧边界；CPU 帧缓冲访问可进入共享 L2。

## bootloader v0.8.0 — 从 Flash 1 MiB 直接执行

- 添加 XIP linker 与映射读取驱动，取消此配置的片内启动 ROM；在 0x007FF000 pinned L2 页使用数据/栈完成 DDR 初始化。
- BIOS 装载入口迁移到 0x00800000，保留 UART 恢复与镜像 ABI/CRC 检查。XIP 启动期间拒绝软件 Flash 安装，防止执行期间占用 SPI 引脚。
- bootloader 与 BIOS 仍为两个镜像。

## hal v0.8.0 — 动态 VRAM 与零基址 DMA

- 添加 hal_video_set_buffers，要求显示关闭、DMA 空闲、缓冲对齐且不重叠；默认帧缓冲由链接器分配，不占用固定顶端 VRAM 窗口。
- 同步音频、Ethernet、USB 和 SD DMA 的零基址内存检查；DDR Flash 驱动等待 XIP busy 释放引脚。

## bios v0.8.0 — 零基址 payload 和实际帧缓冲描述

- BIOS/payload/暂存区迁移到零基址 RAM，BIOS_INFO 返回实际 CSR 帧缓冲地址；同步自检、benchmark 和镜像装载范围。
- payload/audio 上限仍为既有软件预算 0x07E00000。SD 硬件 DMA 单次仍为 4 KiB，较大请求采用分段。

## opensbi v0.8.0 — 零基址 RAM 与当前 CSR 设备树

- FW_TEXT_START 迁移到 0x01000000，probe 的地址、页表索引与用户权限测试同步迁移。
- SD/USB 控制地址从实际 csr.json 提取，设备树使用新 RAM/MMIO 地址；保留 SBI Base/time/atomic/timer/external/U ECALL/Sv32 与 warm reset 验收。

## uboot v0.2.0 — 无 C、正确搬移和有界 SD DMA

- U-Boot 入口迁移到 0x01100000，同步 RAM、装载、栈和控制台地址；关闭 ISA_C，并使用 RV32IM/ILP32 libgcc。
- GCC 编译、LLD PIE 链接并明确 TEXT_BASE，保留动态重定位，修复搬移后命令访问不同 LMB 实例导致 FAT 装载被拒绝的问题。
- SD b_max 限制为 8 扇区，检查 4 KiB bounce buffer 边界，补充 DMA cache 同步和错误状态。已验证原始 32 扇区分段读取、FAT 4 KiB/64 KiB 装载、USB receiver 和 Ethernet ping。

## system v0.7.2 — 首个按组件独立版本的系统包

- 这是第一个用组件版本机制（`VERSIONS.yaml` + `RELEASES.yaml`）发布的系统包：`rtl 0.7.1`、`bootloader 0.7.1`、`hal 0.7.1`、`bios 0.7.2`、`opensbi 0.7.1`、`uboot 0.1.0`。RTL 未改动，沿用 v0.7.1 已验收的 bitstream，不重跑 PnR 与性能。
- 系统包只构建并板测 SoC/bootloader/BIOS；`opensbi`、`uboot` 在 `RELEASES.yaml` 中标为 `external`——它们声明兼容、由各自的流程（`uboot_verify.py`）构建与验收，但不随本包收录。`release.json` 因此记录 `external` 与 `verified_components`，不用六个版本号暗示全部已随包验收。
- 实板验收（同一 bitstream，UART 加载新 BIOS）：`scripts/firmware_verify.py --program` 全部通过（fpu/mmu/isa/l2/fence/uart/irq/ddr/flash/sd/sd blocks/lcd/spi-lcd/io/audio/eth/eth parser/usb/usb tree/dma/usb restart×3/phys/usb input/audio 与 eth 启停、soak 301、usb leds、status）；`scripts/uboot_verify.py --program` 经新 BIOS 的 TFTP 客户端网络启动 `UBOOT.OSB` 成功（1331888 B，933 块 @1428 B，0 重传，380 KiB/s），OpenSBI v1.9 → U-Boot 2026.07 控制台与 `version` 正常。

## bios v0.7.2 — 大镜像网络启动与构建身份分离

- TFTP 客户端支持 RFC 2348 `blksize` 协商（OACK，块大小 1428 B，接收缓冲 1600 B），修复大镜像传输在约 85% 处停滞超时的问题；v0.7.1 的 512 B 客户端下载 1.33 MB 的 `UBOOT.OSB` 会停滞失败，本版实测 933 块、0 重传、380 KiB/s。OACK 重传改为有界循环，只在收到匹配的 ACK 0 后进入 DATA，否则报 `TFTP ACK 0 not received`。
- `arp_request` 改为按目标 IP 请求；`arp_input` 学习服务器 MAC 而不再污染 `server_ip`；新增 `bios_ping`（ICMP echo），ETH 关闭时提供空实现存根以保证链接。
- USB IN 传输增加缓冲区上界参数（`cap`），所有调用点受限，避免越界写。
- 构建身份按组件分离：BIOS 横幅与 `status` 改用 `MINI_APP_ID`（BIOS 自身版本），ROM 横幅继续用 `MINI_BUILD_ID`（解析为 bootloader 版本）。`bootloader=0.7.1`、`bios=0.7.2` 时 ROM 不再误显示 0.7.2；非 BIOS 应用也不再让 ROM 显示 `0.0.0-dev`。Git、输入指纹与产物哈希保持不变。

## uboot v0.1.0 — U-Boot 移植到 riscv-mini

- 首个 U-Boot 移植：基于上游 v2026.07，`firmware/uboot/uboot-port.patch` 记录对上游的最小改动，`scripts/uboot_build.py` 在 WSL 原生 ext4 树可复现地构建（`riscv_mini_defconfig`）。
- 自研 DM 驱动（IP 均经我方修改，上游驱动不匹配）：`liteeth.c`（UCLASS_ETH，32 位访问）、`litesd.c`（UCLASS_MMC，4-bit + HS，f_max 7.5 MHz）、`liteusb.c`（UCLASS_USB，从零写的 PIO 主机控制器，含共享 PHY 复位 F10 处理）。
- 实板验收：`ping` 通、SD 识别 58.2 GiB（4-bit）、USB 枚举 Logitech USB Receiver（Vendor 0x046d Product 0xc52b，HID Boot Keyboard+Mouse），`usb tree` 正常。OpenSBI 运行时 DT 暴露 `ethernet@f0002800`、`mmc@f0008000`、`usb@b1000000`。

## v0.7.1 — 构建身份显示

- ROM、BIOS 与 BIOS `status` 现在打印可追踪的构建身份：`<semver>+<commit7>.<config8>[.dirty]`，其中 `config8` 是构建输入指纹（gateware/firmware 源码、`requirements.lock`、`VERSION` 的 SHA256 前 8 位），同一 `config8` 唯一对应一份构建配置；工作树未提交时带 `.dirty`。
- 三段版本分开：ROM 横幅打印 RTL 版本 `rtl<rtl8>`（`gateware/riscv_mini.v`、`gateware/rtl-manifest.json` 与 CPU RTL 输入的 SHA256 前 8 位），BIOS 横幅打印 ROM 版本 `rom<rom8>`（`firmware/boot.bin` 的 SHA256 前 8 位），BIOS 自身哈希记录在 `validation.json` 的 `boot_image.sha256`。ROM 不打印自身哈希，因为打印会改变被哈希的内容。
- `validation.json` 新增 `build_id`、`rtl_sha256`、`config_sha256`，主机侧检查与实板串口日志因此可互相印证。
- 实板验收：`scripts/firmware_verify.py --program --soak-seconds 60 --mic` 全部通过，ROM/BIOS/`status` 三处身份一致；OpenSBI 复测（SBI Base、time CSR、AMO+LRSC、S timer、S external、U ECALL、Sv32 与 SBI warm reset 后 BIOS 恢复）全部通过，warm reset 后 ROM 再次打印同一身份。
- 功能代码与 VERSION 0.7.1 bump 随本 changelog 条目同提交（`reports/changes/<commit7>.md/.json` 记录构建与实板证据）；tag `v0.7.1` 指向其后的变更记录提交，发布包由该 tag 的干净工作树生成，产物身份不带 `.dirty`。

## v0.7.0 — C 扩展 full 系统与原生 fence

- full MMU/FPU 系统启用 RISC-V C 扩展，boot ROM 从 8 KiB 缩减到 4 KiB，boot 二进制从 5292 B 缩减到 3792 B。
- 采用 CPU 原生 FENCE/FENCE.I，移除外部 fence handshake 和 write-combine 设施，共享 writeback L2 为 CPU 与 DMA 维护一致性。
- DDR 队列深度改为 1，注册跨层路径并更新时序约束；full 配置 PnR setup/hold 0/0，实板指令/MMU/FPU/DDR/外设/音频/DMA 与 60 秒 soak 验收通过。
- 相对追溯版本 0.6.0（f0892da、无 C/8K ROM）提供三轮 all/cache 性能报告；cache suite 两版都有 LCD underflow，真实网络吞吐及 127-cycle ACK 的 FENCE.I 模型边界尚未关闭。

性能报告：[reports/performance/6880dee-vs-f0892da.md](reports/performance/6880dee-vs-f0892da.md)。基线必须显式选择，详见报告身份和边界。

发布二进制、Git hash、构建参数和逐文件 SHA256 见对应版本的 release.json；完整改动以 Git 记录为准，已知失败和未验收项保留在性能/验收报告中。

## v0.6.0 — CPU/DMA 共享一致性 writeback

- 把 0.5.0 的 L2 模式/后端/调度器开关收敛成单一 shared-writeback 设计：CPU 与 DMA 共用一致性 writeback L2，`--l2-mode/--l2-backend/--memory-scheduler/--l2-fast/--l2-posted/--l2-combine/--dma-*` 参数与 `gateware/{cpu_order,dma_scheduler,l2_writeback,write_combine}.py` 一并移除。
- boot 的 DDR 栈改从 L2 RAM 启动；SD 走原生 DMA 客户端；`--l2-size` 只保留 4 KiB/8 KiB，boot ROM 固定 8 KiB，ISA 仍为 rv32imaf（无 C 扩展）。
- 构建包 `build/releases/v0.6.0/full-rv32imaf-rom8k-l24k/`：commit f0892da，place 3/route 2，PnR setup/hold 0/0，Logic 19636/20736、BSRAM 44/46，bitstream `d5f6f8d7…`；实板测试 43 项通过（含 60 s soak 与双麦克风），`--suite all` 与 `--suite cache` 三轮全部 PASS。
- 该阶段的性能基线仍是既有测量 `f0892da-full-rv32imaf-rom8k-l24k-baseline-noc-rom8k-86b26cda`，该包不替换基线，也不构成新的性能结论。

## v0.5.0 — 原生 crossbar 与 CPU fence handshake

- 内存调度改为 crossbar（`gateware/dma_scheduler.py`、`gateware/cpu_order.py`），L2 走原生后端加 burst-refill 快速预取，新增 write-combine 与 posted 写路径实验。
- CPU 侧引入外部 FENCE handshake：`scripts/cpu_fence.py` 给 DBusCachedPlugin 打补丁，生成带 `o_externalFenceRequest/i_externalFenceDone` 的 CPU RTL，`scripts/test_cpu_fence.py` 与 `tests/cpu_fence_tb.v` 验证握手。
- 构建包 `build/releases/v0.5.0/full-rv32imaf-rom4k-l24k/`：commit ff84bfb，place 3/route 2，L2 4 KiB write-through，PnR setup/hold 0/0，Logic 19415/20736、BSRAM 43/46，bitstream `0444301e…`；实板测试 41 项通过，`--suite cpu`、`--suite cache`、`--suite io` 三轮全部 PASS，`--suite mem` 在 1 MiB 的 `read32`/`copy32` 自检失败且可复现，原因见 [docs/version-history.md](docs/version-history.md)。
- 这套外部 fence 与 write-combine 设施在 v0.7.0 被 CPU 原生 fence 取代，`scripts/cpu_fence.py` 已删除；只有该版本的检出能表达这套配置。

## v0.4.0 — 常驻 BIOS、OpenSBI 与 benchmark

- 固件默认改为常驻 BIOS（`firmware/bios/main.c` 与 `firmware/diagnostics/tests.c`），支持 SD/TFTP boot、控制台与设置；新增 `scripts/bios_image.py`、`bios_payload.py`、`bios_tftp.py`、`bios_verify.py`。
- 新增 OpenSBI 构建与验收脚本（`scripts/opensbi_build.py`、`opensbi_verify.py`）和 `scripts/benchmark.py`；libc 与 SD 读路径优化；`scripts/nyancat_assets.py` 提供演示资源。
- CPU RTL 生成改为把 VexRiscv 源码复制到 `ext/VexRiscv` 并修补 CsrPlugin，生成结果与 v0.3.0 不同；默认 Gowin place option 4。
- 构建包 `build/releases/v0.4.0/full-rv32imaf-rom4k-l24k/`：commit ca78a2d，place 4/route 2，PnR setup/hold 0/0，Logic 19117/20736、BSRAM 43/46，bitstream `7abfe464…`；实板测试 39 项通过，`--suite all` 三轮全部 PASS，该提交没有 `cache` 组。

## v0.3.0 — full MMU/FPU 与初始共享 L2

- 启用带 MMU/FPU 的 VexRiscv `linux` 变体（ISA rv32imaf），`scripts/cpu_generate.py` 用 Java 8 + sbt-launch 1.9.7 从 VexRiscv 源码生成四种独立配置的 CPU RTL，`scripts/cpu_qualify.py` 校验生成结果。
- 默认启用 4 KiB 共享 L2（write-through/write-invalidate），boot ROM 固定 4 KiB；SD 改为紧凑 profile，USB 改用 ultra PHY，音频时钟改为 DDS 48 kHz；新增 `scripts/boot_size.py` 与 `scripts/monitor_external_verify.py`。
- 构建包 `build/releases/v0.3.0/full-rv32imaf-rom4k-l24k/`：commit f47e5f2，place 3/route 2，PnR setup/hold 0/0，Logic 18820/20736、BSRAM 43/46，bitstream `768839f7…`；实板测试 38 项通过，该提交没有基准工具。
- CPU RTL 不在 Git 里，包内 `inputs/cpu/` 保存本次重新生成的 `VexRiscv_MmuFpu.v` 与 `generator.json`；与当年实际使用的 RTL 不必然相同。

## v0.2.0 — 外设 HAL 与模块化配置

- 引入按特性开关的 SoC 组装（`gateware/features.py` 与 12 个 `--with-*/--without-*` 开关、`--profile full|minimal`）和 `scripts/build_matrix.py` 特性矩阵，逐特性断言 CSR 与引脚归属。
- 新增 Dock IO、板载 IO/WS2812、原生 SD 后端与 FatFs、音频 DMA 采集与校验、Ethernet、USB HID（OHCI，48 MHz PHY）、单/双麦克风示例程序。
- 仍为 RV32IM `lite` CPU、8 KiB ROM 加 8 KiB 集成 SRAM、无 L2；Gowin place/route 选项硬编码在 `gateware/soc.py`，不能从命令行覆盖。
- 构建包 `build/releases/v0.2.0/full-rv32im-rom8k-l20k/`：commit 60b42a4，PnR setup/hold 0/0，Logic 16275/20736、BSRAM 36/46、rPLL 4/4、PRIMARY/LW 8/8，bitstream `65f206ed…`；实板测试 34 项通过，该提交没有基准工具。注意 USB Host 口接 hub 时 TinyUSB 主机会枚举走到 `TU_ASSERT` 失败路径并执行 `ebreak`，而该 CPU 配置不含 EBREAK 支持，应用会停在 `FAULT cause=00000002`；不接 hub 时全部通过。集成 SRAM 与硬编码选项使当前 `scripts/build.py` 无法表达该配置，只能在 worktree 中构建。

## v0.1.0 — RV32IM 最小系统与 Flash/UART boot

- 建立单一 base SoC：RV32IM `lite` CPU、8 KiB ROM 加 8 KiB 集成 SRAM、无 L2，DDR 运行在 60 MHz 系统 / 120 MHz DDR，SPI 模式 SD 与 FatFs、SPI LCD、RGB 视频。
- 建立 Flash/UART 双入口 boot（`scripts/boot_image.py`、`boot_upload.py`、`boot_verify.py`、`cold_boot.py`）与工具发现脚本 `scripts/configure_tools.py`、环境脚本 `bootstrap.ps1`、`env.ps1`。
- 构建包 `build/releases/v0.1.0/base-rv32im-rom8k-l20k/`：commit 88ece43，PnR setup/hold 0/0，Logic 7347/20736、BSRAM 16/46，bitstream `81ec497f…`；实板测试 20 项通过，用该提交的 `scripts/boot_verify.py`，它没有 `firmware_verify.py`，也没有基准工具。该版本的 `requirements.lock` 是后续版本的子集，缺少 liteeth、liteiclink 与 USB OHCI 数据源。

各版本包的生成方式、补写字段与限制见 [docs/version-history.md](docs/version-history.md)；逐文件 SHA256 用 `scripts/release.py --verify <包路径>` 校验。
