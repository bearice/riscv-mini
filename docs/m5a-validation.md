# M5a HAL / 板级 IO 验证记录

日期：2026-10-01。前置基础版已提交为 `dbf9d520c3bb87d63814f7f2611af8f837cede9b`。M5a 代码、仿真、构建与自动上板检查完成；灯光、四按键和四 DIP 的实物验收仍待用户确认，不能据此标记 M5a 全部验收通过。

## 最终构建

| 项目 | 结果 |
| --- | --- |
| CPU / sys / Wishbone | VexRiscv lite RV32IM，60 MHz |
| DDR | H5TQ1G63EFR-PBC，128 MiB，CK 120 MHz，DLL-off CL6/CWL6 |
| RGB LCD | 480×272 RGB565，9 MHz，525×286 总时序，约 59.94 Hz |
| UART / SPI LCD / SPI-SD / Flash | 115200 baud / 6 MHz / 初始化 400 kHz、运行 6 MHz / 10 MHz |
| 新增时序 | timer1 1 kHz IRQ；按键稳定 5 ms；WS2812B 800 kbit/s，300 µs latch |
| ROM / SRAM | 各 8,192 字节 |
| boot.bin | 6,560 字节；没有 HAL、SD 或 LCD 驱动 |
| app.bin / app.img | 23,956 / 24,004 字节；img 含 48 字节 header |
| 镜像 ABI | `ac75153f`；包含 CSR、内存和 IRQ 分配 |
| Logic / Register | 7,874 / 20,736；4,430 / 16,173 |
| BSRAM / rPLL | 16 / 46；2 / 4 |
| 时序违例 | setup 0，hold 0 |

最终 `riscv_mini.fs` SHA-256：`cedaca2c7140a49dc0610171d32cd873f79a90050ee580d7edf714386f1e551a`。

最终 `app.img` SHA-256：`68063a8b1f1a56b84353250f68471d9602dc528e77666727f81f2e826337e2a1`。

HAL 和 trap/IRQ 全部运行于 DDR 应用。64 位 uptime 的毫秒转换会链接软件除法辅助函数，计入上述应用大小；bootloader 大小没有因此增加。引脚、IRQ 编号与调用约束见 [HAL](hal.md) 和 `gateware/board.json`。

## 自动检查

- `sim/test_board_io.py`：六灯低有效与位序、DIP 同步、短抖动过滤、长按无重复、释放与同时按键均通过。WS2812B 的 RGB→GRB/MSB-first、400/800 ns 高电平、1.25 µs bit 周期、初始/末尾至少 300 µs 低电平，以及 busy 时忽略新提交均通过。
- `sim/test_boot_image.py`：5 项通过，包括 IRQ 分配改变必须改变镜像 ABI。
- 既有 `sim/test_spi.py`、`sim/test_memory.py`、`sim/test_video.py` 通过；视频检查包含正常扫描和欠载恢复。
- 公共 HAL 头在 RV32IM C++ 编译中通过，使用 `extern "C"`、freestanding、`-Wall -Werror`。
- 独立 `firmware/examples/hal_demo.c` 在板上 UART 加载执行：ECALL 进入并返回、自定义异常 handler、至少 20 次 timer IRQ、真实 DDR 上确定性计算结果 `0x73cfeab0`、毫秒 deadline 回绕均通过。串口 `Q` 的 IRQ RX echo 为 `00000051`。示例未进入基础 monitor。
- 最终 Flash/UART 启动回归通过：无效 header CRC、ABI、地址/长度/entry/flags、packet CRC、超时、payload CRC 均被拒绝；有效 UART 应用启动、Flash 安装与加载、两次软件复位自动 Flash 启动正常。SD/FatFs 可列出 `RVTEST00.BIN`，SPI LCD / RGB LCD 初始化标志均为 1，视频欠载为 0。
- 最终 monitor 的 `led 3f` / `led 00` CSR 状态、无效 LED/颜色参数拒绝及 UART IRQ 命令交互通过。最后保留六灯全亮指令及 WS2812B 低亮度红色指令，供实物检查。

Flash 编程使用 Gowin 擦除/编程操作，未调用 Verify，未添加写后读回。启动时和 UART 接收时的镜像 CRC 属于既有加载协议，仍保留。用户已明确跳过断电检查，本轮没有执行冷启动断电验收。没有新增 SD 写入。

## 五分钟记录与人工检查

第一版 M5a bitstream 的串口 `status` / `io` 轮询持续 300.110 秒，共 925 个状态样本；RGB 扫描/完成计数持续增长，所有样本 underflow、UART drops 和 unhandled IRQ 都为 0。timer IRQ 持续增长。此记录是静态画面与串口轮询检查，不是 M4 的 DDR/SD/换帧并发压力测试。其后仅修改应用毫秒延时实现、加强示例计算断言以及将 IRQ 分配纳入镜像 ABI，并对最终版本重新做上述启动回归。

窗口内没有观测到按键边沿或 DIP 变化，因此 `m5a-input.json` 的 `passed:false` 表示未完成全部人工输入覆盖，不能解释为已发现 GPIO 故障，也不能解释为实物输入通过。

| 人工检查 | 当前状态 |
| --- | --- |
| 六灯均可亮、位序与低有效符合实物 | 待确认；已验证 CSR 与仿真 |
| WS2812B 实际红绿蓝颜色 | 待确认；已验证脉冲与 GRB 仿真 |
| Key2 长按/释放无重复，Key3..5 按下/释放 | 待操作；仿真通过 |
| 四位 DIP 各 ON/OFF 读值 | 待操作；仿真通过 |
| 两块 LCD 的最终实物显示 | 待确认；初始化与 DMA 计数正常 |
| F10 共用 PHY reset | 单一输出及 10 ms 低电平释放已接入；Ethernet/USB PHY 功能属于后续阶段 |

实物输入验收可用 monitor 的 `io` 和自动 `BUTTON pressed=... released=...` 输出观察。Key2..5 为 bit0..3；用户 DIP 为四位，B10/RECFG 和 Key1/T10 不参与普通 GPIO 测试。

## 本地原始记录

构建产物与日志在忽略的 `build/` 目录，不随源码提交：

- `build/base/validation.json`、`m5a-build-console.log`：最终大小、资源、时序与哈希。
- `build/base/m5a-demo-uart-console.log`、`m5a-demo-echo.log`：独立 DDR HAL 示例与 UART IRQ echo。
- `build/base/m5a-flash-install-console.log`：最终 FPGA Flash 编程和应用安装。
- `build/base/boot-verification.json`、`boot-verification-uart.log`：最终加载拒绝与正常启动检查。
- `build/base/m5a-input.json`、`m5a-input-uart.log`：五分钟轮询与未完成人工覆盖。
- `build/base/m5a-final-controls.log`：最终 IO 控制和状态检查。

后续仍按 [外围计划](peripheral-hal-plan.md) 推进原生 SD、音频、Ethernet 和 USB Host；本轮不宣称这些设备已经可用。
