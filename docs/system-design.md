# 当前系统设计

描述当前源码的体系结构、地址映射、启动流程、功能开关与构建入口。外设细节见各子系统文档（文末索引）。

## 系统概览

Tang Primer 20K（GW2A-LV18PG256C8/I7）+ Dock 3713 上的裸机 RISC-V 系统。CPU 从片上 4 KiB boot ROM 启动，等待硬件完成 DDR 初始化与训练，把 Flash 或 UART 中的应用镜像载入 DDR，然后在 DDR 中执行；工作 SRAM 为 0，所有驱动与文件系统都在 DDR 应用里。板级验收统一由 DDR monitor 的 `test ...` 命令完成（见 [固件测试命令](firmware-tests.md)）。

- CPU：默认 full 配置 VexRiscv MMU+FPU，RV32IMAF，60 MHz，2 KiB I-cache / 2 KiB D-cache，Sv32，默认启用 4 KiB 共享读缓存 L2；MMU 与 FPU 可独立关闭。CPU RTL 由 `scripts/cpu_generate.py` 生成到 `build/cpu-features/`（`VexRiscv_Base.v` / `_Fpu.v` / `_Mmu.v` / `_MmuFpu.v` + `.yaml`），`gateware/soc.py` 按开关选择文件。
- 时钟：输入 27 MHz，sys 60 MHz，DDR CK 120 MHz。完整来源与复位关系见 [时钟树](clocks.md)。
- 中断映射（RV32 外部中断号，`firmware/hal` 依赖，与生成头一致）：UART0 = 0、timer0 = 1、timer1 = 2、board_io = 3、sdcard = 4、ethmac = 5、usb_host = 6。关闭的功能不占用中断号。
- 应用入口提供 `help`、`status`、`ls`、`reboot`、`io`、`led HH`、`rgb RRGGBB`、`test ...` 与 `!` 复位快捷键。

## 地址映射

| 区域 | 地址 | 说明 |
| --- | --- | --- |
| DDR | `0x40000000` | 128 MiB，单一原生端口，CPU/视频/DMA 共享调度 |
| bootloader 栈 / data / BSS | `0x407fe000` | 8 KiB 保留区，bootloader 使用 |
| DDR 应用 | `0x40800000` | linker 固定入口，4 MiB 上限 |
| RGB 帧缓冲槽 | `0x47e00000` / `0x47e40000` | 双槽，每帧 261,120 B，槽间距 262,144 B，共保留 2 MiB |
| CSR 外设 | `0xF0000000` 起 | 每个外设 2 KiB 对齐槽位，由 LiteX 自动分配 |
| Ethernet MAC | `0xb0000000` | 非缓存 Wishbone |
| USB PIO Host | `0xb1000000` | 非缓存，4 KiB，`CONFIG_USB_ULTRA=1` |

视频帧槽只允许两个固定地址，不能通过 CSR 任意读 DDR；见 [视频规格](video-spec.md)。

## 启动与固件更新

1. FPGA 配置（Flash 前 2 MiB）加载后 ROM 开始执行，等待硬件 DDR ready/training，无栈等待。
2. 硬件 ready 后 ROM 建立保留 DDR 区内的 bootloader 栈。
3. 两秒窗口内按 `b` 进入恢复菜单、按 `u` 走 UART 装载；无有效 Flash 镜像时同样进入恢复菜单。
4. 从 Flash 应用分区 `[2,4)` MiB 自动装载；镜像头含 magic/version、CSR ABI id、load 地址、长度、entry、CRC32，CRC/地址/ABI 不匹配则不执行。
5. 应用跳转到 `0x40800000` 执行。

Flash 写入按用户要求只做擦除与编程，不做写后读回或配置 CRC 比对；Gowin 使用普通 exFlash Erase/Program，不执行 Verify。默认串口 `COM4`、调试器 location `107569`。命令与镜像格式见 [bootloader](bootloader.md)、[硬件启动](hardware-ddr-boot.md)。

```powershell
& $MiniPython .\scripts\boot_upload.py --program --mode uart      # FPGA SRAM + UART 装入 DDR
& $MiniPython .\scripts\boot_upload.py --reset --mode install     # 软件复位并更新应用分区
& $MiniPython .\scripts\boot_upload.py --configure-flash --mode install
& $MiniPython .\scripts\boot_verify.py --program --install
& $MiniPython .\scripts\boot_verify.py --reset --soak-seconds 300
```

## 功能开关与 profile

`scripts/build.py` 默认启用全部功能；`--profile minimal` 或 `--without-NAME` 裁剪外设，关闭项同时从硬件与应用驱动移除。

- `--with-mmu` / `--with-fpu`：独立开关，默认 full 开启两者；minimal 默认关闭。
- `--sd-profile`：`none` / `spi` / `lite` / `full` 四种 profile，默认 lite 原生四位 SDR + Wishbone DMA，SPI 为回退（共用同一组 SD 引脚，需重新下载匹配的配置与应用）。
- `--l2-size`：共享读缓存，默认 `4096`（4 KiB）；`0` 关闭，`8192` 提供但从未构建验证。只缓存读、写直达 DDR，帧缓冲绕过。见 [L2 缓存](l2-cache.md)。
- `--usb-backend`：默认轻量 Ultraembedded PIO Host + TinyUSB；OHCI 为可选回退（`gateware/usb.py` 在构建时把 Gowin IP 复制进 `build/vendor/`）。见 [轻量 USB](usb-light.md)。
- 麦克风、Ethernet、音频、两块 LCD、board_io、WS2812 均为可裁剪功能；组合矩阵由 `scripts/build_matrix.py` 编译验证。

各 profile 的资源与验证结果见 [功能配置](configuration-profiles.md)、[模块化构建](modular-build.md)。

## 构建

```powershell
cd C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini
.\scripts\bootstrap.ps1          # 可重复执行；缺工具时传 -GowinBin / -RiscvBin
. .\scripts\env.ps1
& $MiniPython .\scripts\doctor.py
& $MiniPython .\scripts\build.py --synthesize
```

`build.py` 不加 `--synthesize` 时只生成 RTL/CSR 并编译软件，两种方式都不自动下载板卡。默认产物在 `build/base/`：boot ELF/map/bin、DDR app ELF/map/bin/img、CSR、含 boot ROM 的 RTL、Gowin 工程和 `validation.json`。`validation.json` 的 `firmware_sizes` 记录两级固件的 text/data/BSS 与二进制大小；综合构建记录实际 PnR 的 Logic、Register、CLS、BSRAM、I/O 与全部时钟资源，缺失必要资源或 setup/hold 报告会使构建失败。`board_test` 字段只表示构建脚本未做上板测试。构建把编译器临时目录重定向到 `build/.tmp`，因此 `%TEMP%` 不可写也不会影响构建。

CPU RTL 缺失时构建会提示先运行 `scripts/cpu_generate.py`（需要兼容的 VexRiscv 源码 checkout、Java 8、sbt-launch 1.9.7）。`bootstrap.ps1` 的 smoke 构建使用 `--profile minimal` 写入 `build/bootstrap-smoke`，只依赖 `pythondata-cpu-vexriscv` 自带的 lite CPU RTL，全新 checkout 无需先生成 CPU RTL。

全新 checkout 在 Windows 上的环境前提：`git config --global core.longpaths true`（否则 `uv pip sync` 克隆 `pythondata-cpu-vexriscv` 的嵌套子模块会因路径过长失败：`Filename too long` → `fatal: fetch-pack: invalid index-pack output`）；全局 uv 缓存不可用时把 `UV_CACHE_DIR` 指到短路径，例如 `$env:UV_CACHE_DIR='C:\uv-cache-mini'`。

## 目录

| 路径 | 内容 |
| --- | --- |
| `gateware/` | SoC、SD backend、DDR 端口调度、视频扫描、L2、约束与板级配置 |
| `firmware/boot/` | 共用启动汇编；ROM 无栈等待硬件 DDR ready |
| `firmware/bootloader/` | ROM 装载器、镜像协议、boot / DDR app linker script |
| `firmware/app/` | DDR 基础应用与 monitor |
| `firmware/drivers/` | UART、timer、Flash、SD/FatFs、两块 LCD 的软件接口 |
| `firmware/hal/` | 公共 C/C++ API、trap/IRQ、板级 IO、音频、Ethernet、USB Host |
| `firmware/examples/` | 独立 HAL/SD/音频/网络/麦克风验收应用与键鼠 demo，经 UART 装入 DDR |
| `firmware/vendor/` / `gateware/vendor/` | 固定版本第三方源码、许可证与本地补丁说明 |
| `scripts/` | 环境、构建、镜像打包、上传与板级验收 |
| `sim/` | SPI、DDR 调度、视频扫描、镜像/传输协议等离线验证 |
| `docs/` | 当前系统设计文档 |

## 已知边界

- Flash 启动后的 USB 枚举失败仍未解决（见 [轻量 USB](usb-light.md)）。
- RTOS、HDMI、USB 高速与 Hub/MSC 支持未实现。PT8211 是输出 DAC，板上没有 ADC；外接 I2S 麦克风提供快照输入，连续录音 DMA 尚未实现。
- PRIMARY 与 LW 时钟资源均为 8/8，扩展前需重新评估时钟布线。

## 当前 full 资源与构建身份

配置：MMU+FPU、无 C/B、L2 4 KiB、SD lite、USB ultra、DDS、全部外设。CSR 镜像 ABI 为 `552653d0`；不同功能组合必须重新生成匹配固件，不能把该 ABI 当作所有 profile 的固定值。

| PnR 资源 | 使用 / 可用 |
| --- | ---: |
| Logic | 18,820 / 20,736 |
| Register | 10,771 / 16,173 |
| CLS | 10,118 / 10,368 |
| BSRAM | 43 / 46 |
| PLL | 3 / 4 |
| PRIMARY / LW | 各 8 / 8 |
| IO / IOLOGIC | 139 / 207；62 / 207 |

Gowin V1.9.12.04，place=3、route=2、netlist_hierarchy=0，setup/hold 违例 0/0。本页记录当前默认配置的布局布线资源快照，不表示可以提高运行频率。ROM 配置容量 4096 B、无集成 SRAM；实际固件大小随工具/LTO 构建而变，由链接器强制限制。

Flash 配置偏移 0，匹配应用偏移 0x200000。分区和更新逻辑见 [`firmware/bootloader/main.c`](../firmware/bootloader/main.c) 与 [`scripts/boot_upload.py`](../scripts/boot_upload.py)。历史设计和测试结果由 Git 历史保存，不在 docs 中维护逐轮记录。

## 文档索引

[板级参考](board-reference.md) · [时钟树](clocks.md) · [硬件启动](hardware-ddr-boot.md) · [L2 缓存](l2-cache.md) · [MMU/FPU](cpu-mmu-fpu.md) · [CSR 打包](csr-packing.md) · [模块化构建](modular-build.md) · [功能配置](configuration-profiles.md) · [分模块 RTL](rtl-defaults.md) · [BSRAM 归属](bsram-audit.md) · [HAL](hal.md) · [bootloader](bootloader.md) · [固件测试命令](firmware-tests.md) · [原生 SD](native-sd.md) · [视频规格](video-spec.md) · [音频](audio.md) · [麦克风](microphone.md) · [Ethernet](ethernet.md) · [USB Host](usb.md) · [轻量 USB](usb-light.md) · [键鼠 demo](usb-input-demo.md)
