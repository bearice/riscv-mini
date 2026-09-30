# 开发环境验证记录

日期：2026-09-30。运行环境：Windows / PowerShell。项目根目录：riscv-mini。

## 工具与依赖

| 项目 | 验证结果 |
| --- | --- |
| Python | 项目独立 CPython 3.10.21，uv 创建 .venv |
| uv | 本机 0.12.10 |
| Python 依赖 | requirements.lock 中 20 个包；9 个 Git 来源固定完整 commit，其余固定版本 |
| LiteX / LiteDRAM / LiteSDCard / LiteSPI / litex-boards | 安装版本标识 2026.8，具体来源以 lock 中 commit 为准 |
| RISC-V C/C++ | 复用本机 xPack GCC 14.2.0，RV32IM / ilp32 multilib 可用 |
| Gowin | 复用本机 V1.9.11.02，成功完成实际综合、PnR 和 bitstream 生成 |
| 标准 BIOS 依赖 | picolibc / compiler-rt 源码存在；Meson 1.12.1、Ninja 1.13.2 可执行；标准 BIOS 完整构建尚未验证 |
| 烧录工具 | 本机 Gowin Programmer CLI 可执行；未发现 openFPGALoader |

bootstrap 可重复执行，使用固定 lock；机器工具路径在 .tools.local.json。PATH 只在当前 PowerShell 进程中添加。没有修改全局 Python、永久环境变量、USB 驱动或板上内容。

## 实际验证

1. 运行 scripts/bootstrap.ps1：环境诊断通过，生成最小 SoC 和 CSR JSON/CSV。
2. RV32IM freestanding C 启动程序编译、链接、objcopy 成功，ROM 固件 484 字节；ELF 为 little-endian ELF32 RISC-V，入口地址 0x0。
3. freestanding C++ 示例编译和链接成功；64-bit 除法验证 RV32IM libgcc 库能链接。
4. firmware linker 的 ROM/SRAM 地址与生成 CSR memory map 核对；boot.bin 与生成的 ROM 初始化内容逐字核对。
5. Gowin 综合、布局布线和 bitstream 生成成功，输出 build/m0/gateware/riscv_mini.fs。

本次 M0 PnR 资源：Logic 3,126/20,736，Register 1,479/16,173，BSRAM 13/46，rPLL 1/4。Gowin 从 PLL 自动派生 48 MHz 系统时钟；报告的系统时钟 Fmax 为 69.864 MHz，最差 setup slack 为 +6.520 ns，setup/hold violated endpoints 均为 0。这些数字只适用于当前 M0，不代表加入 DDR、SD 和视频后的整机资源或时序。

综合仍有上游生成 RTL 的常量初值、宽度截断和部分未驱动信号 warning，完整保留在 synthesis.log；不把工具成功解释为零 warning 或硬件行为已验证。首次综合发现 sys reset 被 PLL helper 和自定义同步器同时驱动，已在源代码关闭 helper 的 reset 生成，只由一个同步器统一处理 PLL lock 与 reset 按键，并重新综合通过。

## 当前边界与下一步

更新：下文为 M0 历史记录。随后 M1 已接入 DDR 并上板通过，包括完整 banner、DDR 代码/栈执行和两次 CPU 软件复位；当前状态以 [m1-validation.md](m1-validation.md) 为准。M0 最初第一行乱码的具体原因未单独证明；M1 加入启动等待后输出完整。

用户已确认核心板与 examples/SPI_lcd 对应屏幕连接完成，原有测试程序可以运行。这是现有硬件与示例的验证结果，尚不代表 riscv-mini 的 SPI 驱动或 M0 固件已经上板验证。

2026-09-30 本机只读检查：Windows 识别到 FTDI VID_0403/PID_6010 双通道设备和 USB Serial Port (COM4)；Gowin `--scan-cables F` 枚举到 USB Debugger A 的两个接口，USB location 分别为 107570、107569。对 location 107570 执行 JTAG `--scan` 未及时返回，已结束该扫描进程；尚未确认 FPGA 型号或这些接口与目标板的对应关系。没有下载 bitstream、修改 Flash 或串口发送数据。

随后用户提供 GUI 成功下载截图：目标 GW2A-18C、ID 0x0000081B，实际使用 USB location 107569、2 MHz。按该参数重新 CLI 扫描成功，确认一台兼容 GW2A-18C 的器件；之前扫描选错了 FTDI 接口。

已使用以下命令完成 M0 的 SRAM 下载（退出码 0，日志 Finished，状态码 0x00006020）：

```powershell
& 'C:\Gowin\Gowin_V1.9.11.02_x64\Programmer\bin\programmer_cli.exe' --cable-index 4 --location 107569 --frequency 2MHz --device GW2A-18C --operation_index 2 --fsFile 'C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini\build\m0\gateware\riscv_mini.fs'
```

COM4 / 115200 / 8N1 接收到启动输出后两行与 `> ` 提示符；发送 `m0-echo-check\r`，实际收到 `m0-echo-check\r\n> `，验证 UART 收发与 CPU 执行路径。下载期间第一行 banner 字节乱码，原因尚未定位；不能据此宣称完整启动输出或按键复位已经通过。完整记录保存于 build/m0/hardware-validation.json。此次仅写 SRAM，没有修改 Flash；当前运行 M0 已替换之前运行的 LCD 示例。

当前 bitstream 只启用 CPU、UART、timer/IRQ、ROM/SRAM；DDR、SD、SPI LCD、HDMI 尚未实现。实际缓存为固定上游 VexRiscv_Lite 的 2 KiB I-cache，无 D-cache；规划中的 4 KiB I-cache 后续通过 CPU 配置落实。

下一步为验证按键复位后的完整启动输出，然后建立 H5TQ1G63EFR 的正确时序 profile，接入 LiteDRAM。最终开发配置继续采用 640×480@60 Hz RGB565 双缓冲，30 Hz 提议已撤回。

机器可读报告：build/environment.json 和 build/m0/validation.json。PnR 资源报告：build/m0/gateware/impl/pnr/project.rpt.txt；时序报告：同目录 project_tr_content.html。生成物和绝对机器路径不提交为源文件。
