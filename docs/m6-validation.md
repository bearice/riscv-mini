# M6 原生 SD 验证记录

日期：2026-10-01。前置提交为 M5a `a0ed4a95af89708b66e79f944183b7ceac059171`。本轮原生四位 SD、DMA、FatFs/HAL、五分钟 SD/LCD 并发及最终启动回归通过；用户确认两块屏幕正常稳定，并明确跳过拔插验收。

## 当前配置与构建

| 项目 | 结果 |
| --- | --- |
| CPU / sys / Wishbone | VexRiscv lite RV32IM，60 MHz |
| DDR | H5TQ1G63EFR-PBC，128 MiB，CK 120 MHz，DLL-off CL6/CWL6 |
| SD | 原生 SD memory，初始化一位 400 kHz，运行四位 **7.5 MHz** |
| SD DMA | 双向 sys Wishbone master，共用原 DDR 桥；4 KiB 对齐 bounce，每命令最多八块 |
| RGB LCD / SPI LCD | 480×272 RGB565、9 MHz、约 59.94 Hz / 240×135、6 MHz |
| ROM / SRAM / boot.bin | 8,192 / 8,192 / **6,560 字节** |
| app.bin / app.img | 24,656 / 24,704 字节；img 含 48 字节 header |
| 应用 BSS | 6,252 字节，在 DDR；包含 4 KiB SD bounce |
| 当前镜像 ABI | `039ce734`，包含 CSR、内存和 IRQ |
| Logic / Register | 10,407 / 20,736；5,501 / 16,173 |
| BSRAM / rPLL | **18 / 46；2 / 4** |
| 时序违例 | setup 0，hold 0 |
| SPI 回退 PnR | setup/hold 0；Logic 7,874，Register 4,430，BSRAM 16，rPLL 2 |

最终基础版 `build/base/gateware/riscv_mini.fs` SHA-256：`15e9aa1b7c26e9d50586e8971cbe49503bb6605772f274c8accd2c37d2472f2e`。

最终 `build/base/firmware/app.img` SHA-256：`9a1f60a2bb621e498909b739b63626df5a20253ea2f4bf956101ab7ee51a4505`。

bootloader 没有增加 SD/FatFs/HAL 驱动；boot SHA 随新的 CSR/IRQ ABI 常数改变，大小仍为 6,560 字节。默认构建改为原生 SD；`--sd-backend spi` 保留独立回退。SD event IRQ 在硬件分配为 2，GPIO/timer1 为 3/4；当前 SD 驱动有界轮询，未启用 SD CPU IRQ。

## 初始化与频率问题

最初卡仍在此前 SPI 模式中，卡检测为插入，但原生 CMD55 timeout，状态为 `cmd_event=5`。对 `ls` 初始化/挂载探针连续两次复现。用户第一次整板重启时 Flash 仍为 SPI 基线，因此应用重新将卡初始化为 SPI。

随后先写入原生 FPGA 配置和对应应用，再由用户整板断电重启，原生四位初始化、CSD 容量和 FatFs 根目录读取均通过，错误计数为 0。原生模式与 SPI 模式切换须卡重新上电，见 [原生 SD](native-sd.md)。该问题已在同一初始化/挂载探针上由失败变为通过。

15 MHz 读取通过，包括现有 4 KiB 文件，以及单块/多块未对齐读取。但第一次 CMD24 单块写返回 `cmd_event=1`、`data_event=3`、R1=`00000900`、write status=0、DMA offset=`0x80`，即命令完成且 128 个 word 已供数，数据响应未被接受。将唯一变量写入 divider 从 4 改为 8 后，64 KiB 写入/逐字节读回通过。当前所有正常数据传输固定 7.5 MHz，并重新完整验收；**不声称 15 MHz 写入通过**。进一步确定电气/采样裕量及提速留待后续。

调试标记已从最终驱动移除。早期 15 MHz 失败创建的 `RV6T0000.BIN`、`RV6T0001.BIN` 属于本轮独立测试文件；后续 7.5 MHz 成功文件为 `RV6T0002.BIN` 和正式验收的 `RV6T0003.BIN`。未格式化，也未覆盖既有文件。

## 正式上板验收

- 现有 `RVTEST00.BIN`：4,096 字节，CRC32 **`08040e1e`**；持续轮次保持一致。
- LBA 0..15：16 次单块与一次 16 块请求 CRC 一致，15 MHz 首次值为 `d70426cd`；使用未对齐 caller buffer，请求跨越两个八块 bounce 分段。只读取原有扇区，没有原始 LBA 写入。
- 新文件 `RV6T0003.BIN`：`FA_CREATE_NEW`，65,536 字节；写入、sync/close、重新打开、逐字节模式比对和 CRC32 **`5547fa65`** 均通过。测试工具在 host 上独立计算相同模式的 CRC。
- 五分钟并发：**300.0 秒，134 轮**。每轮 CPU 填写/交换 DDR 帧、SD 读取现有文件并验 CRC、查看设备和中断状态；扫描/完成计数持续增加。全程 SD errors、LCD underflow、UART drops 和 unhandled IRQ 均为 0。
- 轮询计数：RGB frames 从 27,230 到 45,186，completed 从 26,026 到 43,982；末尾 SD read blocks=`0x793`，written blocks=`0x85`。这些包含测试前初始化和文件操作，不能直接作为持续窗口吞吐量。
- 用户明确确认 RGB 色条/白框/灰阶/青黄角标及 SPI LCD 都正常稳定。
- 五分钟使用独立 UART 加载的 `sd_demo.c` 和 `build/m6` 原生 bitstream；最终默认 `build/base` 重新构建/PnR，通过后下载并进行下面的启动回归。没有把最终基础版另跑五分钟，也没有三十分钟测试。
- 最终基础版 Flash 配置和应用安装、有效 UART 启动、Flash 加载及两次软件复位自动 Flash 启动通过；SD 初始化、`ls` 列出测试文件、两块 LCD ready=1、视频欠载为 0。
- 既有 ROM 拒绝检查重新通过：header CRC、ABI、载入地址、framebuffer 区、长度、entry/flags、packet CRC、截断超时和 payload CRC。

Flash 只擦除/编程，未执行 Gowin Verify 或写后读回；加载协议的 CRC 保留。用户实际完成了原生配置下的整板重启，本轮没有再要求最终 7.5 MHz 应用的断电检查。

## 自动检查与限制

`sim/test_sd.py` 通过真实 SDCore 的正确/错误 CRC7 响应、超时完成状态，以及 400 kHz/15 MHz/7.5 MHz clocker 周期。`--upstream` 在锁定版坏 CRC 未暴露的场景失败，本地补丁返回 done/error/crc；不修改 PHY 或 CRC 算法。另通过 GPIO/WS2812、SPI、DDR 共享端口仿真、5 项镜像测试、2 项 programmer 结果测试、Python 语法及公共 HAL C++ 头编译。

本轮实卡为 SDHC；SDSC 初始化/字节地址分支存在但尚无对应实卡。DMA 内存位序与大请求分段通过实际文件和单/多块比较检查；CRC 仿真的 PHY endpoint seam 不代替连接器电气验证。理论四位 7.5 MHz 为 3.75 MB/s，本轮未做吞吐基准，不能把该数字作为实际文件读写速度。

用户要求 **跳过拔插验收**。CD 同步、无卡判断、超时复位和重新 mount 路径已实现，但不声明无卡/插回已上板通过。M5a 的 LED/按键/DIP 人工验收状态仍保持原记录，本轮屏幕确认不替代这些检查。音频、Ethernet、USB Host 留待后续。

## 本地原始证据

忽略的 `build/` 保存日志与产物，不随源码提交：

- `build/base/validation.json`、`build/m6-final-base-build-console.log`：最终构建、大小、哈希、资源、时序。
- `build/m6/sd-verification.json`、`sd-verification-uart.log`：300 秒/134 轮、文件/块检查、计数与结果。
- `build/m6-sd-verification-console.log`：正式验收进度和结果。
- `build/m6/sd-write-probe.log`：15 MHz 单块写失败状态；低频对照见当时的 tool 输出和测试文件。
- `build/m6-final-flash-install-console.log`、`build/base/boot-verification.json`、`boot-verification-uart.log`：最终安装与加载回归。
- `build/m6-spi/validation.json`：SPI 回退编译、PnR 和时序；本轮没有再切换实卡至 SPI。

使用方法见 [原生 SD](native-sd.md)，公共接口见 [HAL](hal.md)。
