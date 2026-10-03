# Flash / UART 装载与基础系统

## 架构与职责

CPU 的复位地址为片上 ROM `0x00000000`。硬件完成 DDR JEDEC 初始化与读训练，ROM 用无栈汇编等待 ready/failure；成功后才启用保留 DDR 工作区，完成 UART/timer、SPI NOR 探测，通过 **Flash 或 UART 两条替代路径**把同一应用装入 `0x40800000`，校验后同步指令缓存并跳转。没有额外的第二级引导程序。

bootloader 仅链接 `start.S`、`uart.c`、`time.c`、精简的 `bootloader/flash.c` 和 `bootloader/main.c`，使用 `-Os -flto` 和 section GC。没有 SD/FatFs、LCD 或软件 DDR 训练。硬件按 lane/bitslip/tap 寻找最大有效窗口并复验中点；旧软件数据位、地址别名和三个 4 KiB 区域检查已移除，不由硬件训练替代。应用 `test ddr` 检查 8 KiB scratch，详见 [硬件启动](hardware-ddr-boot.md)。

SD/FatFs 和两块 LCD 驱动仅存在于独立链接的 DDR 应用中。app 使用 DDR 的代码、数据、BSS 和栈；bootloader 工作区是保留的 8 KiB DDR，没有片上 SRAM。RGB LCD 初始化为黑色双缓冲，SPI LCD 显示基本状态。

## 固定硬件与内存

CPU/sys/Wishbone 60 MHz；DDR CK 120 MHz DLL-off CL6/CWL6；RGB LCD 像素时钟 9 MHz；Flash SPI 10 MHz；SD 原生四位 400 kHz 初始化 / 7.5 MHz 工作（SPI 回退 400 kHz / 6 MHz）；SPI LCD 6 MHz；PT8211 平均 BCK 1.536 MHz / stereo 48,000 Hz，共用 DDS clock-enable。音频仅在 DDR 应用侧驱动，默认静音。默认 CPU 为 MMU+FPU 核，I/D cache 各 2 KiB，无 L2；minimal 使用 lite 核，仅有 2 KiB I-cache。DDR CPU/Wishbone DMA 与视频端口共享单物理 native 端口、视频优先、事务间留八个 sys 周期；协议调度和真实数据路径仍受验证约束。

| 地址 | 用途 |
| --- | --- |
| `0x00000000`，4 KiB | boot ROM，随 FPGA 配置更新 |
| `0x407fe000`，8 KiB | bootloader DDR 工作区，栈顶 `0x40800000` |
| `0x40000000`，128 MiB | DDR 总范围 |
| `0x40300000` | SPI LCD 的 DDR 工作帧 |
| `0x40800000`–`0x40c00000` | 当前 DDR 应用 linker 区域；末尾留 64 KiB 栈 |
| `0x47e00000` / `0x47e40000` | 两个 480×272 RGB565 帧，每帧 261120 字节 |
| DDR 最后 2 MiB | 视频保留区 |

## Flash 分区和写入保护

原理图标注 W25Q32JVS / 4 MiB；本机实际 JEDEC 是 `0x0b4017`，XTX / 8 MiB。驱动还支持 `0xef4016` / `0xef7016` 的 4 MiB 板型；未知 ID 不启用擦写。不是所有核心板都应按本机 8 MiB 假定。

| Flash 偏移 | 大小 | 用途 / 权限 |
| --- | --- | --- |
| `0x000000`–`0x200000` | 2 MiB | FPGA 配置保留区；CPU 只读，禁止擦写 |
| `0x200000`–`0x400000` | 2 MiB | 单一应用镜像；CPU 可擦写 |
| `0x400000`–`0x800000` | 4 MiB，仅本机 | 后续保留；首版 CPU 只读 |

当前 Gowin 配置二进制小于 2 MiB，配置下载地址固定为零；上传工具执行此长度检查。CPU 更新不用 chip erase，只对应用覆盖到的 4 KiB 扇区擦除，按 256 字节页边界编程。先写 payload，最后写镜像头；按用户要求不做 Flash 写后读回校验。更新中断时旧镜像可能失效，启动检查失败后回到 UART 恢复；本版没有 A/B 回滚。

`--configure-flash` 是显式的 FPGA 持久配置更新。工具先执行 Gowin 普通 exFlash Erase/Program（operation 8），再执行 Reprogram 从 Flash 重新配置，最后通过 UART 安装应用。没有 Gowin Verify、独立配置读回或 CRC 比对步骤。不能把普通 `--mode install` 当作 FPGA 配置更新。修改 ROM/CSR/内存布局时需要构建并更新匹配的配置和镜像。

## 镜像与 UART 协议

镜像是 48 字节头加 payload。头为 12 个 little-endian uint32：magic `0x354d5652`、version 1、header 长度、CSR/内存/CPU ABI tag、payload 长度、固定 load `0x40800000`、entry、payload CRC32、flags 0、两个 reserved 0、头部前 44 字节 CRC32。ABI tag 随生成的 CSR、内存映射及 IRQ 编号计算；不是密码签名。

payload 长度为 4 到 2097104 字节；entry 必须四字节对齐并落在 payload 中，完整指令不得跨过尾部。ROM、SRAM、LCD 保留区和任意其他 load 地址均被拒绝。应用本身可以通过正常 HAL 使用其他 DDR 工作区；装载地址限制仅针对镜像。

UART 115200 8N1：loader 输出 `READY HEADER` 后收头；验证通过后输出 `READY DATA`。每包是 uint32 sequence、uint16 count、最多 128 字节数据、覆盖该包前缀和数据的 CRC32；精确顺序和末包长度受检查。成功 ACK 为 `K` 加 uint32 sequence。每个字节等待超时为三秒；失败排空输入并返回 `BL>`。所有数据收完后，再校验 DDR 中的完整 payload。

加载完成使用 `fence` 和 `fence.i`，关闭 machine IRQ，跳转应用入口；应用 `_start` 初始化独立栈、data/BSS，再调用 `main`。M5a 应用的 `hal_init()` 另外安装 trap/IRQ 运行时并启用已注册的 IRQ；bootloader 自身保持轮询，无 HAL/SD/LCD 驱动。RTOS 属于后续工作。

## 恢复和命令

DDR 初始化后等待两秒。无输入默认从 Flash 启动；无有效镜像/Flash 错误时保留 UART 恢复入口。DDR 初始化失败时禁止装载和执行，报告错误后可复位重试。

| boot 菜单字符 | 行为 |
| --- | --- |
| `b` | 自动启动窗口期间进入菜单 |
| `u` | UART 收镜像，装入 DDR 并执行 |
| `p` | UART 收镜像并校验 DDR，然后安装到 Flash 应用区 |
| `f` | 从 Flash 装入 DDR 并执行 |
| `i` | Flash ID 和镜像头状态 |
| `!` | 软件复位 |

DDR 基础应用提供 `help` / `status` / `ls` / `reboot` 和 `!`，M5a 增加 `io` / `led HH` / `rgb RRGGBB` 板级控制。`ls` 不写 SD，最多列出 64 个根目录项。HAL 保留 SD block read/write、Flash 受限读写、SPI 事务、LCD 与视频接口，验收统一通过 `test ...` 子命令进入，不在正常启动时自动执行大范围测试。

## 验证

`scripts/boot_verify.py --program --install` 会写 Flash 应用区，检查 UART/Flash 执行、两次自动 Flash 软件重启、应用状态、SD 根目录读取，并拒绝坏头 CRC、错误 ABI、错误 load、零/越界长度、不对齐/越界 entry、flags、坏包 CRC、UART 截断超时和 payload CRC 不一致。Flash 安装本身不读回；镜像 CRC 检查发生在 UART 接收或启动装载时。完整 UART 日志和验收 JSON 写入 `build/base/`。

`sim/test_boot_image.py` 检查主机格式/边界/CRC，`sim/test_programmer_result.py` 检查 Gowin 失败日志识别；其他仿真范围见 [sim/README.md](../sim/README.md)。`boot_verify.py --reset --soak-seconds 300` 做五分钟只读状态/SD 根目录检查，确认帧/完成计数持续增长、欠载为零，不再执行历史全内存/整帧内容校验。`cold_boot.py` 只监听外部断电重启，不发软件复位。PnR 报告必须 setup/hold 均无违例。硬件冷启动与持续运行结果记录在 [基础版验证](base-validation.md)，不能以软件复位替代断电测试。
