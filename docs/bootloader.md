# Flash / UART 装载与基础系统

## 架构与职责

CPU 的复位地址为片上 ROM `0x00000000`。bootloader 完成 UART/timer、DDR JEDEC 初始化及读训练、必要的有界存储检查、SPI NOR 探测，然后通过 **Flash 或 UART 两条替代路径**把同一应用装入 `0x40800000`，校验后同步指令缓存并跳转。没有额外的第二级引导程序。

bootloader 仅链接 `start.S`、`uart.c`、`time.c`、`flash.c`、`bootloader/main.c`、`boot/ddr.c`，使用 `-Os -flto` 和 section GC。没有 SD/FatFs、SPI LCD、RGB LCD 的软件驱动。DDR 必须先初始化，才能接收或读取 DRAM 中的应用。DDR 初始化保留按 lane/bitslip/tap 寻找最大有效读窗口，以及数据位、地址别名和三个 4 KiB 区域的有界检查；旧的大范围内存和执行压力测试已移除。

SD/FatFs 和两块 LCD 驱动仅存在于独立链接的 DDR 应用中。app 使用 DDR 的代码、数据、BSS 和栈；片上 8 KiB SRAM 是 bootloader 工作区，不包含完整应用。RGB LCD 初始化为黑色双缓冲，SPI LCD 显示基本状态。

## 固定硬件与内存

CPU/sys/Wishbone 60 MHz；DDR CK 120 MHz DLL-off CL6/CWL6；RGB LCD 像素时钟 9 MHz；Flash SPI 10 MHz；SD SPI 400 kHz 初始化 / 6 MHz 工作；SPI LCD 6 MHz。CPU lite 有 2 KiB I-cache，无 D-cache/L2。DDR CPU/视频端口共享单物理 native 端口、视频优先、事务间留八个 sys 周期；协议调度和真实数据路径仍受验证约束。

| 地址 | 用途 |
| --- | --- |
| `0x00000000`，8 KiB | boot ROM，随 FPGA 配置更新 |
| `0x10000000`，8 KiB | boot SRAM / 栈 |
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

当前 Gowin 配置二进制小于 2 MiB，配置下载地址固定为零；上传工具执行此长度检查。CPU 更新不用 chip erase，只对应用覆盖到的 4 KiB 扇区擦除，按 256 字节页边界编程。先写 payload、读回并校验，再最后提交镜像头。更新中断时旧镜像可能失效，自动回到 UART 恢复；本版没有 A/B 回滚。

`--configure-flash` 是显式的 FPGA 持久配置更新。Gowin 配置工具的操作与 CPU 分区擦写不同，因此工具先通过 boundary-scan 更新并校验 FPGA 配置，再执行 Reprogram 从 Flash 重新配置，最后通过 UART 安装应用。本机普通 exFlash 操作曾报告 `SPI Verify failed`，脚本检查错误文本，不能仅相信退出码或 `Finished`。不能把普通 `--mode install` 当作 FPGA 配置更新。修改 ROM/CSR/内存布局时需要构建并更新匹配的配置和镜像。

## 镜像与 UART 协议

镜像是 48 字节头加 payload。头为 12 个 little-endian uint32：magic `0x354d5652`、version 1、header 长度、CSR/内存/CPU ABI tag、payload 长度、固定 load `0x40800000`、entry、payload CRC32、flags 0、两个 reserved 0、头部前 44 字节 CRC32。ABI tag 随生成的 CSR 和内存映射计算；不是密码签名。

payload 长度为 4 到 2097104 字节；entry 必须四字节对齐并落在 payload 中，完整指令不得跨过尾部。ROM、SRAM、LCD 保留区和任意其他 load 地址均被拒绝。应用本身可以通过正常 HAL 使用其他 DDR 工作区；装载地址限制仅针对镜像。

UART 115200 8N1：loader 输出 `READY HEADER` 后收头；验证通过后输出 `READY DATA`。每包是 uint32 sequence、uint16 count、最多 128 字节数据、覆盖该包前缀和数据的 CRC32；精确顺序和末包长度受检查。成功 ACK 为 `K` 加 uint32 sequence。每个字节等待超时为三秒；失败排空输入并返回 `BL>`。所有数据收完后，再校验 DDR 中的完整 payload。

加载完成使用 `fence` 和 `fence.i`，关闭 machine IRQ，跳转应用入口；应用 `_start` 初始化独立栈、data/BSS，再调用 `main`。完整 trap/IRQ/RTOS runtime 是后续工作。

## 恢复和命令

DDR 初始化后等待两秒。无输入默认从 Flash 启动；无有效镜像/Flash 错误时保留 UART 恢复入口。DDR 初始化失败时禁止装载和执行，报告错误后可复位重试。

| boot 菜单字符 | 行为 |
| --- | --- |
| `b` | 自动启动窗口期间进入菜单 |
| `u` | UART 收镜像，装入 DDR 并执行 |
| `p` | UART 收镜像并校验 DDR，然后安装到 Flash 应用区 |
| `f` | 从 Flash 装入 DDR 并执行 |
| `i` | Flash ID 和镜像头状态 |
| `c` | 前 2 MiB 配置保留区 CRC，供更新保护核对 |
| `!` | 软件复位 |

DDR 基础应用仅有 `help` / `status` / `ls` / `reboot` 和 `!`。`ls` 不写 SD，最多列出 64 个根目录项。HAL 保留 SD block read/write、Flash 受限读写、SPI 事务、LCD 与视频接口，旧自检命令不再进入产品 monitor。

## 验证

`scripts/boot_verify.py --program --install` 会写 Flash 应用区，检查 UART/Flash 执行、两次自动 Flash 软件重启、配置保留区 CRC 一致、应用状态、SD 根目录读取，并拒绝坏头 CRC、错误 ABI、错误 load、零/越界长度、不对齐/越界 entry、flags、坏包 CRC、UART 截断超时和 payload CRC 不一致。完整 UART 日志和验收 JSON 写入 `build/base/`。

`sim/test_boot_image.py` 检查主机格式/边界/CRC，`sim/test_programmer_result.py` 检查 Gowin 失败日志识别；其他仿真范围见 [sim/README.md](../sim/README.md)。`boot_verify.py --reset --soak-seconds 300` 做五分钟只读状态/SD 根目录检查，确认帧/完成计数持续增长、欠载为零，不再执行历史全内存/整帧内容校验。`cold_boot.py` 只监听外部断电重启，不发软件复位。PnR 报告必须 setup/hold 均无违例。硬件冷启动与持续运行结果记录在 [基础版验证](base-validation.md)，不能以软件复位替代断电测试。
