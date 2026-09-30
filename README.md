# riscv-mini

Tang Primer 20K + Dock 3713 上的裸机 RISC-V 基础系统。CPU 从小型片上 bootloader 启动，将 Flash 或 UART 中的应用镜像载入 DDR，然后在 DDR 中执行。默认构建只有一个 `base` 配置，旧 M0–M4 和提频试验的阶段分支、测试命令已移除。

## 当前配置

| 项目 | 配置 |
| --- | --- |
| CPU / 总线 | VexRiscv lite RV32IM，60 MHz，2 KiB I-cache，无 D-cache / L2 / MMU |
| 片上存储 | 8 KiB boot ROM、8 KiB 工作 SRAM；SD/LCD 驱动不在 bootloader 中 |
| DDR | H5TQ1G63EFR-PBC，128 MiB，CK 120 MHz，DLL-off，CL6/CWL6 |
| Flash | 本机 JEDEC `0x0b4017`，XTX 8 MiB；独立 SPI，10 MHz |
| UART / timer | 115200 8N1；60 MHz 计数器，当前驱动采用轮询 |
| SD | SPI 模式，初始化 400 kHz，工作 6 MHz，FatFs |
| SPI LCD | 240×135，6 MHz；显示系统和 SD 状态 |
| RGB LCD | 480×272 RGB565，9 MHz，约 59.94 Hz；DDR 双缓冲、8 KiB FIFO |

RGB LCD 基础应用启动时清空两帧，显示黑色画布，供后续图形应用使用。它不再显示旧验收色条、灰阶或角标。扫描器和 DMA 保留，`video_frame()` / `video_present()` 提供写帧和换帧接口。HDMI 暂缓。

应用只保留串口 `help`、`status`、`ls`、`reboot` 命令，以及 `!` 复位快捷键。Flash API 同样可由 DDR 应用调用。GPIO/WS2812B、原生四位 SD、音频、Ethernet、USB Host HID 属于后续工作，见 [外围 HAL 计划](docs/peripheral-hal-plan.md)。完整 trap/IRQ runtime 和 RTOS 尚未接入。

## 开发环境

Windows PowerShell 下使用独立 Python 环境、LiteX/Migen、Gowin FPGA Designer 和 xPack RISC-V GCC。依赖固定在 `requirements.lock`；机器路径写入忽略提交的 `.tools.local.json`。

```powershell
cd C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini
.\scripts\bootstrap.ps1
. .\scripts\env.ps1
& $MiniPython .\scripts\doctor.py
& $MiniPython .\scripts\build.py --synthesize
```

`bootstrap.ps1` 可重复执行；缺少工具时可传 `-GowinBin` 和 `-RiscvBin`。`build.py` 不加 `--synthesize` 时只生成 RTL/CSR 并编译软件；两种方式都不自动下载。默认产物位于 `build/base/`：boot ELF/map/bin、DDR app ELF/map/bin/img、CSR、含 boot ROM 的 RTL、Gowin 工程和 `validation.json`。

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

## 目录

| 路径 | 内容 |
| --- | --- |
| `gateware/` | 单一 SoC、DDR 端口调度、视频扫描、时序约束和板级配置 |
| `firmware/boot/` | 共用启动汇编、仅 bootloader 使用的 DDR 初始化/训练 |
| `firmware/bootloader/` | ROM 装载器、镜像协议、boot / DDR app linker script |
| `firmware/app/` | DDR 基础应用和小型串口入口 |
| `firmware/drivers/` | UART、timer、Flash、SD/FatFs、两块 LCD 的软件接口 |
| `scripts/` | 环境、构建、镜像打包、上传和板级验收 |
| `sim/` | SPI、DDR 调度、视频扫描、镜像/传输协议验证 |
| `docs/` | 当前启动/外围计划；旧阶段文档保留历史证据 |

历史 `--stage`、提频参数、内存/SD 写测/换帧测试命令不再适用于当前源码。旧验收文档记录的是各自当时的实现和结果，不是当前基础版本的测试声明。源码仓库在本目录，父目录 `../docs/` / `../examples/` 为硬件参考资料。
