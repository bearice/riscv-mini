# riscv-mini

Tang Primer 20K + Dock 3713 上的 RISC-V 小型系统。开发入口为 Windows PowerShell，使用项目独立 Python 环境、LiteX/Migen、VexRiscv，以及已有的 Gowin 和 xPack RISC-V GCC。

目标架构：单核 RV32IM / 48 MHz、H5TQ1G63EFR / 128 MiB DDR3、UART monitor、SPI-SD、SPI LCD、480×272 / 约 59.94 Hz RGB565 DDR 双缓冲，输出并行 RGB LCD；HDMI 暂缓。完整设计见 [系统规划](docs/system-plan.md)。机器可读的硬件目标位于 [gateware/board.json](gateware/board.json)。

## 当前实现范围

M0 UART、M1 DDR 和 M2 SPI LCD / SD 已完成构建与上板验证。M2 包含 RV32IM CPU、UART 115200 8N1、timer、32 KiB ROM、16 KiB SRAM、128 MiB DDR、独立 LCD/SD SPI 控制器和 FatFs。裸机 monitor 支持显示图案、挂载 SD、创建测试文件并读回、只读检查已有测试文件。当前使用上游 VexRiscv `lite` 的 2 KiB I-cache，无 D-cache/L2；中断尚未启用。480×272 并行 RGB LCD framebuffer 为当前 M3 开发范围；HDMI 暂缓；M2 的 240×135 SPI LCD 保留独立状态显示用途。

视频目标采用 4.3 英寸 LCD 示例的 9 MHz、525×286 总时序，当前只实现 RGB LCD 的单路 DMA/扫描，HDMI 不生成时钟或占用输出引脚。具体规格、引脚与兼容性条件见 [视频规格](docs/video-spec.md)。

## Bootstrap

需要 PowerShell、Git 和 uv；Gowin 与 xPack GCC 可由脚本发现，或显式提供 bin 目录。依赖按 requirements.lock 安装；不会修改系统 Python，也不会永久修改 PATH。

```powershell
cd C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini
.\scripts\bootstrap.ps1
```

bootstrap 创建 `.venv`（CPython 3.10.21）、同步依赖、发现本地工具，然后运行 doctor 和默认构建验证。已有环境可重复执行。机器路径保存在忽略提交的 `.tools.local.json`。

其他机器需要提供工具目录时：

```powershell
.\scripts\bootstrap.ps1 `
  -GowinBin 'C:\Gowin\Gowin_V1.9.11.02_x64\IDE\bin' `
  -RiscvBin 'C:\xpack-riscv-none-elf-gcc-14.2.0-3\bin'
```

## 开发命令

```powershell
# 每个新 PowerShell 会话执行一次；只影响当前进程。
. .\scripts\env.ps1

# 检查依赖、编译器和路径，输出 build/environment.json。
& $MiniPython .\scripts\doctor.py

# 生成 RTL/CSR，编译 C 启动程序，验证 C++/libgcc，重新生成含固件的 ROM。
& $MiniPython .\scripts\build.py

# 上述步骤后实际调用 Gowin 综合、布局布线；不烧录设备。
& $MiniPython .\scripts\build.py --synthesize
```

默认构建使用 freestanding C/C++，不依赖 shell/Make 编译固件，避免 Windows 与 MSYS 路径混用。LiteX 标准 BIOS 所需 picolibc、compiler-rt、Meson、Ninja 已包含在开发依赖中，但标准 BIOS 的完整 Make 构建不属于本次 M0 验证。

## 目录

| 目录或文件 | 内容 |
| --- | --- |
| gateware/ | SoC 与时钟/复位源、板级目标配置 |
| firmware/boot/ | 启动汇编、linker script、串口启动程序 |
| firmware/apps/ | freestanding C++ 编译/链接样例 |
| scripts/ | bootstrap、当前会话环境、工具发现、doctor、build |
| sim/ | SPI 事务仿真；后续视频 DMA/CDC 仿真 |
| docs/ | 开发验证记录 |
| requirements.in / requirements.lock | 上游固定 commit 和解析后的固定依赖版本 |
| build/ | 生成物与日志，不作为源码维护 |

构建按阶段输出到 `build/m0/`、`build/m1/` 或 `build/m2/`：`csr.json`、`csr.csv`、`firmware/boot.elf`、`boot.bin`、`boot.map`、`elf-info.txt`、`gateware/riscv_mini.v`、Gowin 工程/约束和验证报告。生成目录可再生，不应手工修改。

## 依赖更新

先修改 requirements.in 中明确的上游 commit，再解析 lock；不要在普通 bootstrap 时拉取浮动分支。

```powershell
uv pip compile .\requirements.in --python-version 3.10 -o .\requirements.lock
.\scripts\bootstrap.ps1
```

## 硬件验证

M1 已加入 H5TQ1G63EFR 的 128 MiB DDR，CPU/sys 48 MHz、DDR CK 96 MHz，
无 D-cache/L2 cache。构建保留在独立 build/m1/，默认 build.py 仍构建 M0。

```powershell
. .\scripts\env.ps1
& $MiniPython .\scripts\build.py --stage m1 --synthesize
& $MiniPython .\scripts\board_test.py --stage m1 --program --port COM4 --location 107569 --soft-resets 2
```

第二条命令实际下载到 FPGA SRAM 并验证串口；Flash 不受影响。
COM4 和 USB location 是本机已确认值，换接口/电脑后需重新识别。
board_test.py 下载前核对 bitstream hash 和构建时序报告，并保存下载日志、
UART 日志及 hardware-validation.json。M1 串口输入 `!` 会触发 CPU 软件复位，
重新初始化 DDR 并运行自检；其他字符回显。

M1 验证内容、范围与已知边界见 [m1-validation.md](docs/m1-validation.md)。
M2 构建和下载：

```powershell
& $MiniPython .\scripts\build.py --stage m2 --synthesize
& $MiniPython .\scripts\board_test.py --stage m2 --program --port COM4 --location 107569 --soft-resets 1
# 明确需要创建测试文件时，给 board_test.py 加 --sd-write-test。
& $MiniPython .\sim\test_spi.py
```

M2 启动只读挂载 SD 并在 LCD 显示 RGB 色带、白框和状态文字。
LCD/SD 工作 SPI 为 6 MHz，SD 初始化为 400 kHz，均使用 mode 0 和轮询传输。
串口 monitor 输入一整行后按回车：

| 命令 | 行为 |
| --- | --- |
| `sdinfo` | 重新初始化、挂载、列出前 16 个根目录项；可用于拔卡后恢复 |
| `sdtest` | 新建未占用的 RVTEST00–99.BIN，写 4096 字节并关闭、重新打开、读回校验 |
| `sdcheck RVTEST00.BIN` | 只读检查既有测试文件的大小、每个字节和 CRC32 |
| `lcd` | 重绘 LCD 状态画面 |
| `!` | CPU 软件复位，再次初始化并自检 DDR/外设 |

测试文件使用 FA_CREATE_NEW，已有同名文件不会被覆盖。没有格式化或裸扇区写入命令。
FatFs 配置支持 FAT12/16/32 和 exFAT，本次上板验收使用 FAT32 SDHC；其他卡型/文件系统尚未实测。
完整结果见 [m2-validation.md](docs/m2-validation.md)。

默认命令不连接或烧录开发板。Gowin 工具和 license 需要通过 `--synthesize` 的实际结果验证；doctor 中发现可执行文件不代表 license 有效。板上复位、UART、实际 bank 电压及后续 DDR/HDMI 的验证结果另行记录，不能用构建成功替代上板测试。

本机没有发现 openFPGALoader，已有 Gowin Programmer CLI。使用烧录工具前需确认调试器及驱动；本次 bootstrap 不安装 USB 驱动、不写 FPGA/NOR、不修改 SD 卡。

## M3 并行 RGB LCD

M3 已实现并完成扫描仿真及 Gowin 综合/PnR，已成功 SRAM 下载；DDR、五次软件复位、双缓冲切换、SD 只读校验及 SPI LCD 回归通过，欠载计数为 0。最终版本经用户确认画面正常稳定；先前复位后条纹在本轮未复现，根因未确认，列入 M4 压力测试观察项。
使用 `build.py --stage m3 --synthesize` 构建，`board_test.py --stage m3 --program` 进行明确的 SRAM 下载。
显示为白色外框、RGB 三色带、灰阶及青色/黄色角标。UART 命令 `fbinfo` 读计数，`fbflip` 在垂直消隐切换两帧；`fbcheck` 校验 DMA 整帧像素模和，`fbpattern` / `fbmemory` 切换直接生成图案与 DDR 帧缓冲。
详细当前验证状态见 [m3-validation.md](docs/m3-validation.md)，扫描测试为 `sim/test_video.py`。
