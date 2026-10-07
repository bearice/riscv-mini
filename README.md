# riscv-mini

Tang Primer 20K + Dock 3713 上的裸机 RISC-V 基础系统。CPU 从片上 8 KiB boot ROM 启动，使用 L2 RAM 作为启动栈，
由软件完成 DDR 初始化与训练，把 Flash 或 UART 中的应用镜像载入 DDR，然后在 DDR 中执行。
默认 DDR 固件为常驻 BIOS，提供 POST、LCD/UART 文字终端、USB 键盘、图形与 IO 服务，
支持从 SD/TFTP 引导自定义裸机程序；OSB1 可一次性交接给 OpenSBI，进入 S-mode，见 [OpenSBI](docs/opensbi-port.md)。接口与用法见 [BIOS](docs/bios.md)。
验收统一为 BIOS/DDR monitor 的 `test ...` 命令。系统整体结构、地址映射、中断映射与文档索引见
[系统设计](docs/system-design.md)；板级引脚与电气约束见 [板级参考](docs/board-reference.md)。

当前代码发布为 **v0.7.0**（C/MMU/FPU、4 KiB ROM），版本依据见 [Git 里程碑追溯](docs/version-history.md)。
提交前按 [SOP](docs/pre-commit-sop.md) 保存范围和适用报告；仅代码发布更新 [changelog](CHANGELOG.md)，文档/流程提交另存范围记录。代码提交后从 clean Git hash 重建最终二进制并归档。
构建与历史产物入口见 [目录说明](docs/build-artifacts.md)，本地 `build/README.md` 列出当前实板版本、基线和最新候选。

## 当前配置

| 项目 | 配置 |
| --- | --- |
| CPU / 总线 | full 默认 VexRiscv MMU+FPU，RV32IMAF，60 MHz，2 KiB I/D-cache、Sv32、4 KiB 共享 writeback L2；可独立关闭 MMU/FPU |
| 片上存储 | 8 KiB boot ROM、无独立工作 SRAM；软件初始化 DDR，bootloader 栈/data/BSS 复用 4 KiB L2 RAM；SD/LCD/音频/网络/USB 驱动均在 DDR 应用中 |
| DDR | H5TQ1G63EFR-PBC，128 MiB，CK 120 MHz，DLL-off，CL6/CWL6 |
| Flash | 本机 JEDEC `0x0b4017`，XTX 8 MiB；独立 SPI，10 MHz |
| UART / timer | 115200 8N1；应用 UART IRQ RX，timer0 ticks/uptime，timer1 1 ms IRQ |
| SD | 原生四位 SD lite + 128-bit coherent DMA，初始化 400 kHz、读 15 MHz / 写 7.5 MHz，FatFs；可选 SPI 回退 |
| SPI LCD | 240×135，6 MHz；显示系统和 SD 状态 |
| RGB LCD | 480×272 RGB565，9 MHz，约 59.94 Hz；DDR 双缓冲、8 KiB FIFO |
| 音频 | PT8211 PCM16 stereo，48,000 Hz 共用 DDS；512 帧 FIFO、DDR ring DMA；默认静音 |
| Ethernet | RTL8201F + LiteEth RMII，100 Mbps 全双工；50 MHz PHY REF_CLK，MDIO、2 RX / 2 TX packet slots、Flash UID 派生 MAC |
| USB Host | USB3317 + Ultraembedded PIO Host + TinyUSB；FS 12 Mbit/s，sys 60 MHz 串行收发，IRQ、Boot 键盘/鼠标和 raw HID HAL；可选 OHCI 回退 |

完整频率、时钟来源和复位关系见 [时钟树](docs/clocks.md)。默认轻量 USB 移除了 48 MHz PLL，
当前 PLL 为 3/4；ULPI 仅用于 USB3317 初始化，仍保留相移 60 MHz PLL。CPU/DDR 保持 60/120 MHz。
当前 USB 实现、资源和验收见 [轻量 USB](docs/usb-light.md)，后端选择与 OHCI 回退见
[USB Host](docs/usb.md)。PRIMARY 和 LW 仍为 8/8，扩展前需重新评估时钟布线。

MMU/FPU 独立开关、SD none/spi/lite/full 四种 profile 及 48 kHz 共用 DDS 见
[功能配置](docs/configuration-profiles.md)。full 默认 MMU+FPU、SD lite；minimal 默认关闭
MMU/FPU。分模块 RTL 的转换语义与模块粒度见 [分模块 RTL](docs/rtl-defaults.md)。

软件 DDR 启动、L2 启动 RAM和函数大小见 [DDR 启动](docs/ddr-boot.md)。
默认包含 4 KiB 共享 writeback L2（`--l2-size 4096`）：CPU/音频 32-bit，LCD/SD lite 128-bit coherent，帧缓冲不分配缓存行，
后端直接连接 LiteDRAM native crossbar；启动 RAM 要求 L2 至少 4 KiB。缓存路径和一致性约束见 [L2 缓存](docs/l2-cache.md)。
MMU/FPU 的资源与布线边界见 [CPU 能力](docs/cpu-mmu-fpu.md)。

RGB LCD 默认显示 BIOS 的 80×34 字符终端；图形模式提供 RGB565 双帧槽。独立基础 monitor
（`--app firmware/examples/monitor.c`）启动时显示黑色画布；扫描器和 DMA 保留，
`video_frame()` / `video_present()` 提供写帧和换帧接口，见 [显示规格](docs/video-spec.md)。HDMI 暂缓。
独立 [键鼠图形 demo](docs/usb-input-demo.md) 在大 LCD 上显示鼠标光标、键盘文字和输入状态，
通过 UART 装入 DDR；源码为 `firmware/examples/usb_input_demo.c`。

复位后的本机网络初始化流量会短时占满两个未过滤 RX 槽，并发窗口前显式等待其结束；
限制与失败现场见 [Ethernet](docs/ethernet.md)。Flash 启动后首次 USB 枚举存在未定位的间歇失败，
`test usb restart` 可恢复，见 [轻量 USB](docs/usb-light.md)。

C/C++ HAL、machine trap/IRQ、六个 LED、四用户按键、四位 DIP、WS2812B 和 F10 共用 PHY reset
的接口与示例见 [HAL](docs/hal.md)。原生四位 SD 见 [原生 SD](docs/native-sd.md)，PT8211 与
DDR ring DMA 见 [音频](docs/audio.md)，外接 I2S 麦克风见 [麦克风](docs/microphone.md)。
RTOS 属于后续工作。

## 固件测试命令

构建功能开关、最小配置及分模块 Verilog 输出见 [模块化构建](docs/modular-build.md)。默认启用
全部功能，使用 `--profile minimal` 或 `--without-NAME` 裁剪外设；关闭的功能同时从硬件和应用
驱动中移除，未启用功能的测试命令返回 `UNSUPPORTED`。

接线以用户更正为准：麦克风 DA=P11、CK=R11、LR=M15、WS=J16，另接 GND/3V3；T9 继续用于
WS2812。双麦克风 demo 为 `firmware/examples/microphone_stereo_demo.c`，第二只接 DA=T6、
CK=R8、LR=T8、WS=P9；monitor 命令为 `test mic stereo`。

输入 `test` 列出 DDR、Flash、UART/IRQ、SD、两块 LCD、板级 IO、音频、Ethernet、USB 和并发
测试命令。完整参数、读写影响和外部验收边界见 [固件测试命令](docs/firmware-tests.md)。例如：

```text
test ddr
test sd blocks
test lcd
test usb restart
test audio
test soak 300
test lcd clear
```

网络收发使用 `test eth start/stop`，真实 HID 输入使用 `test usb input`；两秒音调使用显式
`test audio tone`。这些检查只在请求时执行，boot ROM 不包含任何测试代码。批量自动验收使用
`scripts/firmware_verify.py --reset --soak-seconds 300`，脚本调用固件命令，不要求 USB 拔插或物理键盘输入。

## 开发环境

Windows PowerShell 下使用独立 Python 环境、LiteX/Migen、Gowin FPGA Designer 和 xPack RISC-V GCC。
依赖固定在 `requirements.lock`；机器路径写入忽略提交的 `.tools.local.json`。

```powershell
cd C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini
.\scripts\bootstrap.ps1
. .\scripts\env.ps1
& $MiniPython .\scripts\doctor.py
& $MiniPython .\scripts\build.py --synthesize
# SPI 回退拥有相同 SD 引脚，需要重新下载匹配的 FPGA 配置和应用。
& $MiniPython .\scripts\build.py --sd-backend spi --output-dir build/spi --synthesize
```

`bootstrap.ps1` 可重复执行；缺少工具时可传 `-GowinBin` 和 `-RiscvBin`。smoke 构建使用
`--profile minimal` 写入 `build/bootstrap-smoke`，只依赖 `pythondata-cpu-vexriscv` 自带的 lite
CPU RTL，因此全新 checkout 无需先生成 CPU RTL。`build.py` 不加
`--synthesize` 时只生成 RTL/CSR 并编译软件；两种方式都不自动下载。默认产物位于 `build/runs/<时间>-<Git版本>-<配置>-<用途>/`：
boot ELF/map/bin、DDR app ELF/map/bin/img、CSR、含 boot ROM 的 RTL、Gowin 工程和 `validation.json`。
正式发布的二进制、报告和参数保存在 `build/releases/v<版本>/<配置>/`，普通代码提交快照保存在 `build/archives/v<版本>-<Git hash>/<配置>/`；可清理的中间 run 与保留的复现 recipe 分开。`build/README.md` 和 `scripts/builds.py list` 提供当前验收版本、基线和最近构建入口。代码发布变化跟踪在 [CHANGELOG.md](CHANGELOG.md)，改动说明、验证和性能依据保存在 `reports/`；完整范围直接查 Git。

全新 checkout 在 Windows 上的两个环境前提：先执行 `git config --global core.longpaths true`，
否则 `uv pip sync` 克隆 `pythondata-cpu-vexriscv` 的嵌套子模块会因路径过长失败
（`Filename too long` → `invalid index-pack output`）；若全局 uv 缓存不可用
（`Failed to initialize cache ... sdists-v9\.git`），把 `UV_CACHE_DIR` 指到短路径，例如
`$env:UV_CACHE_DIR='C:\uv-cache-mini'`。默认 full profile（MMU+FPU）还需要先生成 CPU RTL
`build/cpu-features/VexRiscv_MmuFpu.v`，命令与依赖见 [CPU 能力](docs/cpu-mmu-fpu.md)；
未生成时 `build.py` 会直接报 `Missing CPU RTL`。构建把编译器临时目录重定向到 `build/.tmp`，
`%TEMP%` 不可写时也不会失败。

`validation.json` 的 `firmware_sizes` 记录两级固件的 text/data/BSS 和二进制大小；综合构建还记录
实际 PnR 的 Logic、Register、CLS、BSRAM、I/O 和所有时钟资源。缺失必要资源或 setup/hold 报告会使
构建失败。`board_test` 字段只表示构建脚本未做上板测试，实板结果记录在独立验收 JSON 和验证文档中。
软件构建不产生新的 PnR 证据，建议使用不同输出目录保存 FPGA 构建与独立示例。

## 启动和固件更新

bootloader 使用 L2 启动栈进行软件 DDR 初始化，然后从 Flash 自动装载应用。等待期间按 `b` 进入恢复菜单，
或按 `u` 走 UART。无有效 Flash 镜像时也进入恢复菜单。CRC、地址或 ABI 不匹配的镜像不会执行。

```powershell
# 先下载 FPGA SRAM，再通过 UART 将应用装入 DDR 执行。
& $MiniPython .\scripts\boot_upload.py --program --mode uart

# 已运行基础应用时：软件复位，更新 Flash 的应用分区。
& $MiniPython .\scripts\boot_upload.py --reset --mode install

# 更新 FPGA 的持久配置，然后安装匹配的应用镜像。
& $MiniPython .\scripts\boot_upload.py --configure-flash --mode install

# Flash/UART 错误边界及启动验收；--install 会写应用分区。
& $MiniPython .\scripts\boot_verify.py --program --install

# 五分钟只读持续检查：状态、SD 根目录、DDR 视频扫描计数和欠载。
& $MiniPython .\scripts\boot_verify.py --reset --soak-seconds 300
```

默认串口 `COM4`、调试器 location `107569`，其他连接使用 `--port` / `--location`。工具检查 PnR 和
bitstream hash。普通应用更新只擦写 Flash `[2,4)` MiB，前 2 MiB FPGA 配置由 CPU 驱动的地址范围保护。
Flash 写入仅擦除和编程，不做写后读回或配置 CRC 比对；Gowin 使用普通 exFlash Erase/Program，不执行 Verify。
配置更新会替换 FPGA bitstream，应与匹配的 `app.img` 一起更新。

### BIOS 迭代：RAM 装载与 Flash 刷写

临时测试优先 RAM 装载（UART → DDR 执行，不写 Flash）；验收通过后再刷 Flash 应用分区：

```powershell
# 仅重建固件（gateware 未变，不加 --synthesize）。参数必须与板端 bitstream 一致，
# 从当前 run 的 validation.json 抄取 cpu-variant/cpu-verilog/sd-profile/usb-backend/rom-size/l2-size。
& $MiniPython .\scripts\build.py --profile full --app firmware\bios\main.c --cpu-variant linux `
    --cpu-verilog build\releases\v0.7.1\full-rv32imafc-rom4k-l24k\inputs\cpu\VexRiscv_MmuFpu.v `
    --usb-backend ultra --sd-profile lite --audio-clock dds --rom-size 4096 --l2-size 4096 --purpose bios-iter

# RAM 装载运行：'!' 软件复位 → 恢复菜单 → UART 传输 → DDR 执行，不触碰 Flash。
& $MiniPython .\scripts\boot_upload.py --reset --mode uart --image <run>\firmware\app.img

# 验收后刷入 Flash 应用分区，并从 Flash 启动。
& $MiniPython .\scripts\boot_upload.py --reset --mode install --image <run>\firmware\app.img
& $MiniPython .\scripts\boot_upload.py --mode flash
```

`--mode install` 要求目标 run 目录有通过 PnR/时序验证且 bitstream hash 未变的 `validation.json`；
镜像 ABI 由 CSR 派生，仅改固件时不变，可直接刷入现役 bitstream 的板子。

### FPGA 重启与卡死恢复

| 状态 | 恢复方法 |
| --- | --- |
| BIOS/monitor 正常运行 | BIOS `reboot` 命令或 setup 菜单 `!`（软件复位，重入 ROM 装载器） |
| 固件卡死（如 U-Boot `### ERROR ### Please RESET the board`） | 断电重启；ROM 装载器只在 FPGA 上电/复位时运行，卡死后串口无菜单 |
| 免断电重跑 gateware | `boot_upload.py --program`（调试器把 bitstream 写入 SRAM，FPGA 立即运行新配置） |
| 重新加载 Flash 中的配置 | 调试器 `--operation_index 1`（Flash reload），`--configure-flash` 写入后自动执行 |
| 更新持久 gateware | `boot_upload.py --configure-flash --mode install`（bitstream 写 FPGA Flash + reload + 安装匹配 `app.img`） |

### TFTP 引导服务器

`boot net` 需要主机侧 TFTP 服务器；UDP69 防火墙规则绑定的是 Python313 解释器，`.venv` 的 python 被防火墙阻断：

```powershell
& C:\Users\bearice\AppData\Local\Programs\Python\Python313\python.exe .\scripts\bios_tftp.py `
    --bind 169.254.25.153 --file build\uboot\firmware\UBOOT.OSB --name UBOOT.OSB
```

板端 `boot net <file>` 的文件名必须与 `--name` 一致；payload 校验只接受 load=0x41000000 的 OS 载荷
（OSB1/OSB2，如 `UBOOT.OSB`），BIOS 自身的 `app.img`（load=0x40800000）会被拒绝。
ARP 解析每 250 ms 重试一次，最长 10 秒；link 位由 HAL 每 250 ms 轮询刷新，首包 `HAL_NO_MEDIA` 属正常。

详细启动流程、镜像格式、分区、恢复命令和验证范围见 [bootloader](docs/bootloader.md)。
当前默认 full 配置的结构、时钟和资源见
[系统设计](docs/system-design.md)。

## 目录

| 路径 | 内容 |
| --- | --- |
| `gateware/` | SoC、可选 SD backend、DDR 端口调度、视频扫描、时序约束和板级配置 |
| `firmware/boot/` | 共用启动汇编；ROM 使用 L2 启动 RAM 建立栈 |
| `firmware/bootloader/` | ROM 装载器、镜像协议、boot / DDR app linker script |
| `firmware/bios/` | 默认常驻 BIOS：POST、TTY、设置、系统服务与 SD/TFTP 引导 |
| `firmware/diagnostics/` | BIOS 与 monitor 共用的板级自检命令 |
| `firmware/common/` | 固件与示例共用的绘图、网络辅助代码 |
| `firmware/drivers/` | UART、timer、Flash、SD/FatFs、两块 LCD 的软件接口 |
| `firmware/hal/` | 公共 C/C++ API、trap/IRQ、板级 IO、音频、Ethernet 和 USB Host |
| `firmware/examples/` | 可选 monitor、HAL/SD/音频/网络/键鼠应用；bios_demo.c 使用二级程序 SDK |
| `firmware/vendor/` / `gateware/vendor/` | 固定版本第三方源码、许可证与本地补丁说明 |
| `scripts/` | 环境、构建、镜像打包、上传和板级验收 |
| `sim/` | SPI、DDR 调度、视频扫描、镜像/传输协议验证 |
| `docs/` | 当前系统设计、板级参考和各子系统接口说明 |

源码仓库在本目录，父目录 `../docs/` / `../examples/` 为硬件参考资料。

历史设计和测试记录由 Git 历史保留；docs 仅维护当前设计，不依赖本地构建证据。
