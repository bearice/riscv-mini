# riscv-mini

Tang Primer 20K + Dock 3713 上的裸机 RISC-V 基础系统。CPU 从小型片上 bootloader 启动，将 Flash 或 UART 中的应用镜像载入 DDR，然后在 DDR 中执行。默认 `base` 使用原生 SD，另保留 SPI-SD 回退构建。旧 M0–M4 和提频试验的阶段分支已移除；M10 将板上验收统一为 DDR monitor 的 `test ...` 命令。

## 当前配置

| 项目 | 配置 |
| --- | --- |
| CPU / 总线 | full 默认 VexRiscv MMU+FPU，RV32IMAF，60 MHz，2 KiB I/D-cache、Sv32，无 L2；可独立关闭 MMU/FPU |
| 片上存储 | 8 KiB boot ROM、8 KiB 工作 SRAM；SD/LCD/音频/网络/USB 驱动均在 DDR 应用中 |
| DDR | H5TQ1G63EFR-PBC，128 MiB，CK 120 MHz，DLL-off，CL6/CWL6 |
| Flash | 本机 JEDEC `0x0b4017`，XTX 8 MiB；独立 SPI，10 MHz |
| UART / timer | 115200 8N1；应用 UART IRQ RX，timer0 ticks/uptime，timer1 1 ms IRQ |
| SD | 原生四位 SDR + Wishbone DMA，初始化 400 kHz、工作 7.5 MHz，FatFs；可选 SPI 回退 |
| SPI LCD | 240×135，6 MHz；显示系统和 SD 状态 |
| RGB LCD | 480×272 RGB565，9 MHz，约 59.94 Hz；DDR 双缓冲、8 KiB FIFO |
| 音频 | PT8211 PCM16 stereo，48,000 Hz，平均 BCK 1.536 MHz；512 帧 FIFO、DDR ring DMA；默认静音 |
| Ethernet | RTL8201F + LiteEth RMII，100 Mbps 全双工；50 MHz PHY REF_CLK，MDIO、2 RX / 2 TX packet slots、Flash UID 派生 MAC |
| USB Host | USB3317 + Ultraembedded PIO Host + TinyUSB；FS 12 Mbit/s，sys 60 MHz 串行收发，IRQ、Boot 键盘/鼠标和 raw HID HAL；可选 OHCI 回退 |

完整频率、时钟来源和复位关系见 [时钟树](docs/clocks.md)。默认轻量 USB 移除了 48 MHz PLL，当前 PLL 为 3/4；ULPI 仅用于 USB3317 初始化，仍保留相移 60 MHz PLL。CPU/DDR 保持 60/120 MHz。当前 USB 实现、资源和验收见 [轻量 USB](docs/usb-light.md)；[M10 收尾](docs/m10-review.md) 保留原 OHCI 基线。PRIMARY 和 LW 仍为 8/8，扩展前需重新评估时钟布线。

MMU/FPU 独立开关、SD none/spi/lite/full 四种 profile 及 48 kHz 共用 DDS 见 [配置与验证](docs/configuration-profiles.md)。full 默认 MMU+FPU、SD lite；minimal 默认关闭 MMU/FPU。分模块 RTL 诊断见 [默认值修复](docs/rtl-defaults.md)。

RGB LCD 基础应用启动时清空两帧，显示黑色画布，供后续图形应用使用。它不再显示旧验收色条、灰阶或角标。扫描器和 DMA 保留，`video_frame()` / `video_present()` 提供写帧和换帧接口。HDMI 暂缓。

独立 [键鼠图形 demo](docs/usb-input-demo.md) 在大 LCD 上显示鼠标光标、键盘文字和输入状态，通过 UART 装入 DDR；源码为 `firmware/examples/usb_input_demo.c`。

M9 自动枚举/软件复位及五分钟稳定并发结果见 [M9 验证](docs/m9-validation.md)。
2026-10-01 已完成实际键盘和鼠标输入验收，并修复 OHCI 多端点共用 TD 的故障，见 [输入验证](docs/usb-input-validation.md)。该补丁尚未写入 Flash。并发窗口前显式等待复位后的本机网络初始化
流量结束；立即复位后两个未过滤 RX 槽可能溢出并丢 UDP，限制与失败现场均记录在验证页。

M5a 已接入 C/C++ HAL、machine trap/IRQ、六个 LED、四用户按键、四位 DIP、WS2812B 和 F10 共用 PHY reset。应用提供 `help`、`status`、`ls`、`reboot`、`io`、`led HH`、`rgb RRGGBB`、`test ...`，以及 `!` 复位快捷键。接口和示例见 [HAL](docs/hal.md)，实物验收状态见 [M5a 验证](docs/m5a-validation.md)。M6 原生四位 SD 见 [原生 SD](docs/native-sd.md) / [M6 验证](docs/m6-validation.md)。M7 PT8211/FIFO/DDR ring DMA 已接入，见 [音频](docs/audio.md) / [M7 验证](docs/m7-validation.md)。M8 Ethernet 已接入，见 [Ethernet](docs/ethernet.md) / [M8 验证](docs/m8-validation.md)。M9 USB Host HID 已接入，基础 monitor 在 UART 输出键盘 usage 的按下/释放通知，见 [USB](docs/usb.md)。RTOS 属于后续工作，见 [外围 HAL 计划](docs/peripheral-hal-plan.md)。

## 固件测试命令

构建功能开关、最小配置及分模块 Verilog 输出见 [模块化构建](docs/modular-build.md)。默认启用全部功能，使用 `--profile minimal` 或 `--without-NAME` 裁剪外设；关闭的功能同时从硬件和应用驱动中移除。

外接 I2S 麦克风和大 LCD 波形 demo 见 [麦克风](docs/microphone.md)。接线以用户更正为准：DA=P11、CK=R11、LR=M15、WS=J16，另接 GND/3V3；T9 继续用于 WS2812。
双麦克风 demo 为 `firmware/examples/microphone_stereo_demo.c`，第二只接 DA=T6、CK=R8、LR=T8、WS=P9；使用共享 I2S 时序分别显示两路波形，monitor 命令为 `test mic stereo`。T9 保留为独立 WS2812 输出。

输入 `test` 列出 DDR、Flash、UART/IRQ、SD、两块 LCD、板级 IO、音频、Ethernet、USB 和并发测试命令。完整参数、读写影响和外部验收边界见 [固件测试命令](docs/firmware-tests.md)。例如：

```text
test ddr
test sd blocks
test lcd
test usb restart
test audio
test soak 300
test lcd clear
```

网络收发使用 `test eth start/stop`，真实 HID 输入使用 `test usb input`；两秒音调使用显式 `test audio tone`。这些检查只在请求时执行，boot ROM 不包含任何测试代码。批量自动验收使用 `scripts/firmware_verify.py --reset --soak-seconds 300`，脚本调用固件命令，不要求 USB 拔插或物理键盘输入。

## 开发环境

Windows PowerShell 下使用独立 Python 环境、LiteX/Migen、Gowin FPGA Designer 和 xPack RISC-V GCC。依赖固定在 `requirements.lock`；机器路径写入忽略提交的 `.tools.local.json`。

```powershell
cd C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini
.\scripts\bootstrap.ps1
. .\scripts\env.ps1
& $MiniPython .\scripts\doctor.py
& $MiniPython .\scripts\build.py --synthesize
# SPI 回退拥有相同 SD 引脚，需要重新下载匹配的 FPGA 配置和应用。
& $MiniPython .\scripts\build.py --sd-backend spi --output-dir build/spi --synthesize
```

`bootstrap.ps1` 可重复执行；缺少工具时可传 `-GowinBin` 和 `-RiscvBin`。`build.py` 不加 `--synthesize` 时只生成 RTL/CSR 并编译软件；两种方式都不自动下载。默认产物位于 `build/base/`：boot ELF/map/bin、DDR app ELF/map/bin/img、CSR、含 boot ROM 的 RTL、Gowin 工程和 `validation.json`。

`validation.json` 的 `firmware_sizes` 记录两级固件的 text/data/BSS 和二进制大小；综合构建还记录实际 PnR 的 Logic、Register、CLS、BSRAM、I/O 和所有时钟资源。缺失必要资源或 setup/hold 报告会使构建失败。`board_test` 字段只表示构建脚本未做上板测试，实板结果记录在独立验收 JSON 和验证文档中。软件构建不产生新的 PnR 证据，建议使用不同输出目录保存 FPGA 构建与独立示例。

## 启动和固件更新

bootloader 初始化和训练 DDR，等待两秒，然后从 Flash 自动装载应用。等待期间按 `b` 进入恢复菜单，或按 `u` 走 UART。无有效 Flash 镜像时也进入恢复菜单。CRC、地址或 ABI 不匹配的镜像不会执行。

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

默认串口 `COM4`、调试器 location `107569`，其他连接使用 `--port` / `--location`。工具检查 PnR 和 bitstream hash。普通应用更新只擦写 Flash `[2,4)` MiB，前 2 MiB FPGA 配置由 CPU 驱动的地址范围保护。按用户要求，Flash 写入仅擦除和编程，不做写后读回或配置 CRC 比对；Gowin 使用普通 exFlash Erase/Program，不执行 Verify。配置更新会替换 FPGA bitstream，应与匹配的 `app.img` 一起更新。

详细启动流程、镜像格式、分区、恢复命令和验证范围见 [bootloader](docs/bootloader.md)。
当前默认 full 配置的时序、资源、音频/网络及五分钟实板验收见
[2026-10-03 验收](docs/full-validation-2026-10-03.md)；RV32GCB 仅综合的
对照结果见 [资源实验](docs/rv32gcb-resources.md)。

## 目录

| 路径 | 内容 |
| --- | --- |
| `gateware/` | SoC、可选 SD backend、DDR 端口调度、视频扫描、时序约束和板级配置 |
| `firmware/boot/` | 共用启动汇编、仅 bootloader 使用的 DDR 初始化/训练 |
| `firmware/bootloader/` | ROM 装载器、镜像协议、boot / DDR app linker script |
| `firmware/app/` | DDR 基础应用和小型串口入口 |
| `firmware/drivers/` | UART、timer、Flash、SD/FatFs、两块 LCD 的软件接口 |
| `firmware/hal/` | 公共 C/C++ API、trap/IRQ、板级 IO、音频、Ethernet 和 USB Host |
| `firmware/examples/` | 独立 HAL/SD/音频/网络验收应用和 RGB LCD 键鼠 demo，通过 UART 装入 DDR |
| `firmware/vendor/` / `gateware/vendor/` | 固定版本第三方源码、许可证与本地补丁说明 |
| `scripts/` | 环境、构建、镜像打包、上传和板级验收 |
| `sim/` | SPI、DDR 调度、视频扫描、镜像/传输协议验证 |
| `docs/` | 当前启动/外围计划；旧阶段文档保留历史证据 |

历史 `--stage`、提频参数和旧阶段命令不再适用于当前 monitor；当前验收使用上面的 `test ...` 命令。旧验收文档记录的是各自当时的实现和结果，不是当前基础版本的测试声明。源码仓库在本目录，父目录 `../docs/` / `../examples/` 为硬件参考资料。
