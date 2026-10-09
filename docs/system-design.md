# 当前系统设计

描述当前源码的体系结构、地址映射、启动流程、功能开关与构建入口。外设细节见各子系统文档（文末索引）。

## 系统概览

Tang Primer 20K（GW2A-LV18PG256C8/I7）+ Dock 3713 上的裸机 RISC-V 系统。CPU 从片上 8 KiB boot ROM 或 4 MiB Flash XIP 启动（v0.8.0 默认 XIP，复位入口 0xf3100000 对应物理 Flash 1 MiB，无片内 ROM），在 L2 启动 RAM 上运行软件 DDR 初始化与训练，把 Flash 或 UART 中的应用镜像载入 DDR，然后在 DDR 中执行；工作 SRAM 为 0，所有驱动与文件系统都在 DDR 应用里。板级验收统一由 BIOS 的 `test ...` 命令完成（见 [固件测试命令](firmware-tests.md) · [性能测试](benchmark.md)）。

- CPU：默认 full 配置 VexRiscv MMU、无 FPU，RV32IMA，60 MHz，2 KiB I-cache / 2 KiB D-cache，Sv32，默认启用 4 KiB 共享 writeback L2（CPU/音频 32-bit，LCD 16-bit / SD lite 32-bit，内存侧 128-bit coherent）；MMU 与 FPU 可独立关闭。CPU RTL 由 `scripts/cpu_generate.py` 生成到 `build/cpu-features/`（`VexRiscv_Base.v` / `_Fpu.v` / `_Mmu.v` / `_MmuFpu.v` + `.yaml`），`gateware/soc.py` 按开关选择文件，使用原生 cache/fence 实现。
- 时钟：输入 27 MHz，sys 60 MHz，DDR CK 120 MHz。完整来源与复位关系见 [时钟树](clocks.md)。
- 中断映射（RV32 外部中断号，`firmware/hal` 依赖，与生成头一致）：UART0 = 0、timer0 = 1、timer1 = 2、board_io = 3、sdcard = 4、ethmac = 5、usb_host = 6。关闭的功能不占用中断号。
- 默认 DDR 固件为 [常驻 BIOS](bios.md)：POST、UART/LCD TTY、USB 键盘、图形、IO、自检与 SD/TFTP 裸机引导。设置保存于 SD 的 BIOS.CFG，启动代码负责 DDR 初始化和 Flash/UART 装载。独立基础 monitor 可用 `--app firmware/examples/monitor.c` 构建。

MMU 配置另有 60 MHz 的独立 64 位机器定时器，接 MTIP；S-mode 外设 mask/pending CSR 为 `0x9c0` / `0xdc0`。OpenSBI 的 OSB1 一次性交接与保护边界见 [OpenSBI](opensbi-port.md)。

SD lite 与 LCD 分别使用 32-bit / 16-bit 客户端接口，由 [共享内存控制器](memory-controller.md) 的独立缓冲转换到共享 L2 的 coherent 128-bit 入口，音频保留 32-bit Wishbone DMA，Ethernet 使用 CPU 访问 packet SRAM。详细路径见 [DMA](native-dma.md)。

## 地址映射

| 区域 | 地址 | 说明 |
| --- | --- | --- |
| Flash XIP | `0xf3000000..0xf33fffff` | Wishbone，4 MiB 只读、可缓存；物理 1 MiB 对应复位入口 0xf3100000 |
| DDR | `0x00000000..0x07ffffff` | 128 MiB，CPU/LCD/SD 经共享 L2；后端为 LiteDRAM native crossbar |
| bootloader 栈 / data / BSS | `0x007ff000..0x007fffff` | 4 KiB 保留区，训练前由 L2 RAM 提供，训练后按 writeback 保留到 DDR |
| DDR 应用 | `0x00800000` | linker 固定入口，4 MiB 窗口 |
| RGB 帧缓冲槽 | base0/base1 CSR 指定 DDR 地址 | 16 字节对齐，每帧 261,120 B；BIOS 分配双缓冲，U-Boot 单缓冲；CPU 访问可分配 L2 行 |
| CSR 外设 | `0xf0000000` 起 | 每个外设 2 KiB 对齐槽位，由 LiteX 自动分配 |
| Ethernet MAC | `0xf1000000` | Wishbone MMIO，2 RX / 2 TX packet slots |
| USB Host | `0xf2000000` | Wishbone，4 KiB 窗口，Ultra PIO / OHCI 寄存器 |

帧地址由软件通过 `base0/base1` CSR 配置，完整一帧须位于 DDR 内；见 [视频规格](video-spec.md)。

## 启动与固件更新

统一操作入口是 `./mini.ps1`（Python `scripts/mini.py`），详见 [任务指南](../scripts/README.md)。

1. FPGA 配置加载后，L2 固定 4 KiB 启动 RAM，启动代码在 `0x007ff000` 建立栈/data/BSS。
2. 启动代码执行软件 DDR 初始化与读训练，成功后解除缓存固定，保持同一栈地址继续装载。
3. 两秒窗口内按 `b` 进入恢复菜单、按 `u` 走 UART 装载；无有效 Flash 镜像时同样进入恢复菜单。
4. 从 Flash 应用分区（`0x200000` 起）自动装载；镜像头含 magic、CSR ABI id、load 地址（`0x00800000`）、长度、entry、CRC32，CRC/地址/ABI 不匹配则不执行。
5. 应用跳转到 `0x00800000` 执行。

日常运行与验证优先使用 SRAM 加载和 DDR 运行，不触碰 Flash；持久更新使用 `scripts/mini.py board update --build <ID>`，支持 ROM 与 XIP 双流程，并在写入后对应用及启动区执行逐字节精确读回比较。

```powershell
./mini.ps1 board status                                          # 被动观察 UART
./mini.ps1 board recover --build current                         # SRAM 加载，进入 boot 菜单
./mini.ps1 board run --build current                             # SRAM gateware + UART 应用到 DDR
./mini.ps1 board update --build current --dry-run                # 持久更新预检
./mini.ps1 board verify --build current --suite firmware         # 固件功能验收
./mini.ps1 board verify --build current --suite flash            # 精确 Flash 读回比较
./mini.ps1 board verify --build current --suite cold             # 外部断电冷启动观察
```

## 功能开关与 profile

`scripts/build.py` 默认启用全部功能；`--profile minimal` 或 `--without-NAME` 裁剪外设，关闭项同时从硬件与应用驱动移除。

- `--with-mmu` / `--with-fpu`：独立开关，默认 full 开启两者；minimal 默认关闭。
- `--sd-profile`：`none` / `spi` / `lite` / `full` 四种 profile，默认 lite 原生四位 SDR + 32-bit DMA 端口和内存侧 burst buffer，SPI 为回退（共用同一组 SD 引脚，需重新下载匹配的配置与应用）。
- `--l2-size`：共享 writeback 缓存，默认 `4096`（4 KiB）；启动 RAM 要求至少 4 KiB，`8192` 提供但尚未实板验收。CPU 帧缓冲访问不分配缓存行。见 [L2 缓存](l2-cache.md)。
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

`build.py` 不加 `--synthesize` 时只生成 RTL/CSR 并编译软件，两种方式都不自动下载板卡。默认产物在 `build/runs/<时间>-<Git版本>-<配置>-<用途>/`：boot ELF/map/bin、DDR app ELF/map/bin/img、CSR、含 boot ROM 的 RTL、Gowin 工程和 `validation.json`。`validation.json` 的 `firmware_sizes` 记录两级固件的 text/data/BSS 与二进制大小；综合构建记录实际 PnR 的 Logic、Register、CLS、BSRAM、I/O 与全部时钟资源，缺失必要资源或 setup/hold 报告会使构建失败。`board_test` 字段只表示构建脚本未做上板测试。构建把编译器临时目录重定向到 `build/.tmp`，因此 `%TEMP%` 不可写也不会影响构建。

CPU RTL 缺失时构建会提示先运行 `scripts/cpu_generate.py`（需要兼容的 VexRiscv 源码 checkout、Java 8、sbt-launch 1.9.7）。`bootstrap.ps1` 的 smoke 构建使用 `--profile minimal` 写入 `build/bootstrap-smoke`，只依赖 `pythondata-cpu-vexriscv` 自带的 lite CPU RTL，全新 checkout 无需先生成 CPU RTL。

全新 checkout 在 Windows 上的环境前提：`git config --global core.longpaths true`（否则 `uv pip sync` 克隆 `pythondata-cpu-vexriscv` 的嵌套子模块会因路径过长失败：`Filename too long` → `fatal: fetch-pack: invalid index-pack output`）；全局 uv 缓存不可用时把 `UV_CACHE_DIR` 指到短路径，例如 `$env:UV_CACHE_DIR='C:\uv-cache-mini'`。

## 目录

| 路径 | 内容 |
| --- | --- |
| `gateware/` | SoC、SD backend、共享缓存与 native 端口、视频扫描、L2、约束与板级配置 |
| `firmware/boot/` | 共用启动汇编；ROM 使用 L2 启动 RAM 建立栈 |
| `firmware/bootloader/` | ROM 装载器、镜像协议、boot / DDR app linker script |
| `firmware/bios/` | 默认常驻 BIOS、公开服务 ABI 与裸机 SDK |
| `firmware/diagnostics/` | BIOS 与 monitor 共用自检 |
| `firmware/common/` | 共用绘图与网络辅助代码 |
| `firmware/drivers/` | UART、timer、Flash、SD/FatFs、两块 LCD 的软件接口 |
| `firmware/hal/` | 公共 C/C++ API、trap/IRQ、板级 IO、音频、Ethernet、USB Host |
| `firmware/examples/` | 独立 HAL/SD/音频/网络/麦克风验收应用与键鼠 demo，经 UART 装入 DDR |
| `firmware/vendor/` / `gateware/vendor/` | 固定版本第三方源码、许可证与本地补丁说明 |
| `scripts/` | 环境、构建、镜像打包、上传与板级验收 |
| `sim/` | SPI、native DMA、视频扫描、镜像/传输协议等离线验证 |
| `docs/` | 当前系统设计文档 |

## 已知边界

- Flash 启动后的 USB 枚举失败仍未解决（见 [轻量 USB](usb-light.md)）。
- DDR 软件训练依赖 L2 固定启动窗口；当前电源、温度与其他板型不继承已连接板卡的启动验收。边界见 [DDR 启动](ddr-boot.md)。
- RTOS、HDMI、USB 高速与 MSC 支持未实现；全速 Hub 和其后的全速 HID 已支持，容量与限制见 [轻量 USB](usb-light.md)。PT8211 是输出 DAC，板上没有 ADC；外接 I2S 麦克风提供快照输入，连续录音 DMA 尚未实现。
- PRIMARY 与 LW 时钟资源均为 8/8，扩展前需重新评估时钟布线。

## 当前已验收 C 配置

2026-10-06 的 `build/c-fit/ddr-depth1-p2` 使用原生 C+MMU+FPU CPU（2 KiB I/D cache、两周期 I-cache 和 injector 寄存器）、4 KiB ROM/L2、全部外设，保持 sys 60 MHz / DDR 120 MHz。DDR bank command queue 深度 1，关闭 auto precharge；默认 Gowin place 2/route 2。CPU 不导出外部 fence/atomic 信号。

资源为 Logic 19797/20736（16473 LUT）、Register 11251/16173、CLS 10295/10368、BSRAM 42/46、PLL 3/4；setup/hold 违例 0/0。音频 13 次、DMA、41 轮约 61 秒综合压力测试及启动边界检查通过。当前默认 CPU 生成仍关闭 C、ROM 默认 8 KiB，此处身份仅对应明确选择 C 核及 4 KiB ROM 的产物。

ABI `ba18270e`；bitstream SHA256 `fcb9ccc9c6abc3c0ff0df8c1121e1d0fd9816de49c8f1703d4b4ee05bb0517a4`，app.img SHA256 `72a3d70580420c63f5e59864e44549ad6a820a215453b0f274ca1692e394684a`。本轮只下载 SRAM/UART，没有更新 Flash。旧 bitstream 的布局敏感错误尚未唯一定位，慢 ACK 模型的 FENCE.I 边界仍未关闭；完整对照、复现命令和声学/视觉/网络验收范围见 [调试与验收纪要](debug-c-pnr-handoff.md)。

## 历史无 C full 基线资源与构建身份

配置：MMU+FPU、无 C/B、共享 writeback L2 4 KiB、LCD/SD 128-bit coherent、CPU fence 握手、SD lite、USB ultra、DDS、全部外设。CSR 镜像 ABI 为 `88bc82d4`；不同功能组合必须重新生成匹配固件，不能把该 ABI 当作所有 profile 的固定值。

| PnR 资源 | 使用 / 可用 |
| --- | ---: |
| Logic | 19,636 / 20,736 |
| Register | 11,115 / 16,173 |
| CLS | 10,247 / 10,368 |
| BSRAM | 44 / 46 |
| PLL | 3 / 4 |
| PRIMARY / LW | 各 8 / 8 |
| IO / IOLOGIC | 139 / 207；62 / 207 |

Gowin V1.9.12.04，place=3、route=2、netlist_hierarchy=0，setup/hold 违例 0/0。上述基线使用移除补丁前的 CPU fence 握手；当前源码已删除它及 L2 atomic bypass，不能继承上述资源、时序和实板验收。最新 C 候选见 [PnR 交接](debug-c-pnr-handoff.md)。基线实板验收包含固件自检、连续 LCD 计时器、SD、静音音频和外部网络并发，不表示其他裁剪组合或更高频率已通过上板验收。ROM 配置容量 8192 B、无集成 SRAM；实际固件大小随工具/LTO 构建而变，由链接器强制限制。

Flash 配置偏移 0，匹配应用偏移 0x200000。分区和更新逻辑见 [`firmware/bootloader/main.c`](../firmware/bootloader/main.c) 与 [`scripts/boot_upload.py`](../scripts/boot_upload.py)。历史设计和测试结果由 Git 历史保存，不在 docs 中维护逐轮记录。

## 文档索引

[板级参考](board-reference.md) · [时钟树](clocks.md) · [DDR 启动](ddr-boot.md) · [L2 缓存](l2-cache.md) · [MMU/FPU](cpu-mmu-fpu.md) · [CSR 打包](csr-packing.md) · [模块化构建](modular-build.md) · [功能配置](configuration-profiles.md) · [分模块 RTL](rtl-defaults.md) · [BSRAM 归属](bsram-audit.md) · [HAL](hal.md) · [bootloader](bootloader.md) · [BIOS](bios.md) · [OpenSBI](opensbi-port.md) · [GW-BASIC 解释器](gwbasic.md) · [固件测试命令](firmware-tests.md) · [性能测试](benchmark.md) · [原生 SD](native-sd.md) · [视频规格](video-spec.md) · [音频](audio.md) · [麦克风](microphone.md) · [Ethernet](ethernet.md) · [USB Host](usb.md) · [轻量 USB](usb-light.md) · [键鼠 demo](usb-input-demo.md)
