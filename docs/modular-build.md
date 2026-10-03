# 模块化构建

默认 full 构建启用全部现有功能、MMU+FPU 和 SD lite，并生成实际的 Verilog 模块层级。CPU（VexRiscv）、DDR、UART、8 KiB boot ROM、8 KiB SRAM、计时器/IRQ 和系统控制器
构成固定内核：当前启动协议要求把应用装入 DDR，因此这些不作为可关闭外设。
CPU/sys 60 MHz、DDR 120 MHz 不变。
默认 Gowin `place_option=3`、`route_option=2`、`netlist_hierarchy=0`，可用
`--place-option` / `--route-option` 覆盖。最终完整验收见
[2026-10-03 full 验收](full-validation-2026-10-03.md)。

## 功能开关

每个功能同时接受 `--with-NAME` 和 `--without-NAME`。`--profile full` 是
默认值；`--profile minimal` 仅保留内核和 Flash。关闭 Flash 后为 UART-only
装载，仍然检查镜像 ABI/CRC，不能安装或自动加载 Flash 应用。

| NAME | 功能 | 依赖 |
| --- | --- | --- |
| `mmu` | Sv32、M/S/U；full 默认开启 | 匹配生成的 CPU RTL |
| `fpu` | 单精度 FPU；full 默认开启，与 MMU 独立 | 匹配生成的 CPU RTL |
| `flash` | 启动/应用 SPI Flash 驱动与控制器 | 内核 |
| `spi-lcd` | 240×135 SPI LCD 控制器和驱动 | 无其他可选功能 |
| `sd` | microSD 控制器、块读写驱动 | `--sd-profile none/spi/lite/full`，默认 lite |
| `filesystem` | FatFs、挂载和文件操作 | `sd` |
| `video` | 480×272 RGB LCD、DDR 显示 DMA、9 MHz PLL | DDR 内核 |
| `board-io` | 六单色 LED、四用户按键、四 DIP、按键 IRQ | 无其他可选功能 |
| `ws2812` | T9 上的 WS2812 输出 | 独立于 `board-io` 和麦克风 |
| `audio` | PT8211 PIO、DDR PCM DMA 与 HAL | DDR 内核 |
| `mic` | 第一组 I2S 麦克风、24-bit 512 样本快照 FIFO | 无新增 PLL |
| `mic-stereo` | 第二组麦克风、48-bit 512 对样本 FIFO | `mic` |
| `eth` | RTL8201F RMII、MAC、包 SRAM、HAL | `flash`，保留 Flash UID 派生 MAC |
| `usb` | USB3317、PIO Host、HID HAL，sys60 串行 PHY；一颗初始化 PLL | 可选 OHCI 回退 |

未明确启用的默认依赖项会随父功能一起关闭：例如 `--without-sd` 同时关闭
默认的 FatFs，`--without-flash` 同时关闭默认 Ethernet。若明确指定互相冲突
的开关，则构建立即报错；例如 `--with-filesystem --without-sd`。最小配置
下启用依赖功能时需一起指定其父功能。

CPU 能力、SD profile 和 DDS 音频配置的完整命令及验证边界见
[配置说明](configuration-profiles.md)。`full` profile 默认启用全部外设，
MMU/FPU 默认开启；minimal 默认关闭，两者仍可分别覆盖。音频默认平均 48 kHz 的共用 DDS，保留旧整数分频作对照。

```powershell
# 全功能、独立模块 Verilog，运行 Gowin 综合/布局布线。
.venv/Scripts/python.exe scripts/build.py --synthesize --output-dir build/full

# 内核 + Flash；不占用显示和 USB PLL。
.venv/Scripts/python.exe scripts/build.py --profile minimal --synthesize --output-dir build/minimal

# 只经 UART 启动。
.venv/Scripts/python.exe scripts/build.py --profile minimal --without-flash --output-dir build/uart-only

# 内核 + Flash + 原生 SD 块接口，不链接 FatFs。
.venv/Scripts/python.exe scripts/build.py --profile minimal --with-sd --output-dir build/sd-blocks

# 最小双麦克风波形系统；USB 按键控制可另加 --with-usb。
.venv/Scripts/python.exe scripts/build.py --profile minimal --with-video --with-mic --with-mic-stereo --app firmware/examples/microphone_stereo_demo.c --output-dir build/mic-only

# 从全功能系统裁掉网络和音频输出。
.venv/Scripts/python.exe scripts/build.py --without-eth --without-usb --without-audio --output-dir build/capture
```

`--synthesize` 才执行 Gowin；否则只生成 RTL 和编译固件。构建不下载 FPGA，
不改写 Flash。已知示例的必要功能由构建脚本检查，避免直到编译时才出现
缺失 CSR 的错误。自定义应用可包含 `features.h` 检查 `MINI_FEATURE_*`。

## Verilog 文件

默认使用 LiteX 的 hierarchical converter，保留 SoC 各功能块的模块实例和显式端口。
块内的 FSM、FIFO、CSR 实现内联在所属模块中；当前 full 配置输出 35 个模块，
外部 CPU 和 USB 核另列入源文件列表。`--deep-verilog` 可用于诊断完整内部层级，
但完整层级的布线结果不能替代默认配置的验收。文件
布局为：

```text
gateware/
  riscv_mini.v                         顶层连接及未能独立划分的公共逻辑
  modules/
    riscv_mini__ddrphy.v
    riscv_mini__sdram.v
    riscv_mini__rgb_lcd.v               仅启用 video 时存在
    riscv_mini__audio.v                 仅启用 audio 时存在
    riscv_mini__mic.v                   仅启用 mic 时存在
    riscv_mini__usb_host.v              仅启用 usb 时存在
    ...                                其他外设、CSR bank、总线等 SoC 块
  rtl-manifest.json                     生成的模块名与文件映射
  sources.f                            RTL 源列表，包括外部 CPU/OHCI 核
  run.tcl                              Gowin 工程包含所有所需源文件
```

这不是把扁平逻辑按文本片段分文件：模块层级先由 LiteX 生成，再将完整的
`module...endmodule` 输出到不同文件。顶层仍含公共连接和 converter 必须
内联的逻辑；综合器可跨层级优化，不承诺布局布线后的物理层级原样保留。
`--flat-verilog` 保留兼容输出，适合比对或不支持层级转换的工具流程。

原有 CDC 和引脚时序约束会按实际模块路径限定：包括向量每一位的第一同步器
级、USB reset net 和外部 OHCI 核内部路径。第二级同步器与数据通路继续参与
时序检查。没有因为拆模块而加入全局 false path。

层级转换前会从最终完整设计传播每条同步语句的实际时钟域，修正 Migen
对父模块做 `ClockDomainsRenamer` 后、子模块原始 sync 字典仍保留旧域名的
情况。这尤其影响 Ethernet RX 的嵌套计时器；只改 SDC 不能修正错误的硬件
时钟连接。该处理保留 reset/CE 条件，且不修改安装的 Migen/LiteX 包。

## 固件与镜像

开关同时决定硬件实例、引脚请求、显示/USB PLL、IRQ、应用链接的驱动及
monitor 测试代码。未启用外设不生成假 CSR，也不访问不存在的地址。
保留的 HAL 接口返回 `HAL_UNSUPPORTED`，状态结构清零、输出数量为 0；
无返回值的 stop/poll 和板级 setter 为无操作。`hal_sd_init()` 可以在关闭
FatFs 时初始化原始块设备，`hal_sd_mount()` 则需要 `filesystem`。

monitor `status` 输出启用功能；`test` 仅列出可用测试。调用被关闭外设的
测试命令返回 `UNSUPPORTED`，不会输出虚假的 PASS。并发 soak 需要
`filesystem/video/usb/audio` 都启用。bootloader 仍只链接 UART、计时器、
DDR 初始化和可选 Flash，不链接外设 HAL。

配置标志参与镜像 ABI，即使关闭 FatFs 或第二只麦克风没有改变 CSR 地址，
应用也不能误装到另一个配置。`validation.json` 保存最终功能列表、时钟、
RTL 模式/manifest、固件大小、ABI，以及请求综合时的资源与时序结果。

## 验证方法

```powershell
# 29 个全功能/最小/独立启用/独立关闭/SD backend 组合，无上板操作。
.venv/Scripts/python.exe scripts/build_matrix.py --jobs 2

# 使用该构建配置筛选固件测试；关闭模块的命令也检查 UNSUPPORTED。
.venv/Scripts/python.exe scripts/firmware_verify.py --program --output-dir build/minimal

# 全功能外接麦克风已连接时，增加单路及双路快照检查。
.venv/Scripts/python.exe scripts/firmware_verify.py --program --mic --output-dir build/full
```

配置矩阵检查固件编译、CSR bank 和实际引脚是否随开关移除，使用 flat RTL
加快组合检查；真实多文件 Gowin 与实板验收另行记录，不能用矩阵通过替代。
`sim/test_features.py` 检查依赖冲突和跨配置镜像拒绝，`sim/test_rtl.py` 检查
向量同步器、reset net、外部核路径的层级约束转换，以及嵌套模块的时钟域、
reset/CE 保留，I2S 仿真仍覆盖 mono/stereo。

## 本轮验收（2026-10-02）

| 项目 | 全功能 | 最小（内核 + Flash） |
| --- | ---: | ---: |
| 生成模块数（含顶层） | 276 | 123 |
| Logic | 16,275 / 20,736 | 6,420 / 20,736 |
| Register | 8,714 / 16,173 | 3,665 / 16,173 |
| CLS | 9,131 / 10,368 | 4,432 / 10,368 |
| BSRAM | 36 / 46 | 12 / 46 |
| PLL | 4 / 4 | 1 / 4 |
| IO | 139 / 207 | 56 / 207 |
| Setup / hold 违例端点 | 0 / 0 | 0 / 0 |
| Bootloader 字节 | 6,568 | 6,560 |
| Monitor 镜像字节（含头） | 68,624 | 8,968 |
| 镜像 ABI | `a17706ee` | `f64f7df1` |

证据：`build/modular-full-good/validation.json`、
`build/modular-minimal-final/validation.json`；最小 monitor 经裁掉状态输出后的
大小见 `build/modular-minimal-app/validation.json`。资源为 Gowin 完成布局布线
后的报告，不是只凭配置推算。

`build/feature-matrix-final/result.json` 记录 29 种配置编译及 CSR/引脚检查全部
通过；`build/evidence/modular-reuse-flat.log`、`build/evidence/modular-reuse-hier.log` 记录同目录
切换 RTL 模式后均能编译并正确验证 ROM 初始化。

全功能实板执行 39 次固件命令检查，最小实板执行 17 次，均通过，报告分别在
`build/modular-full-good/firmware-verification.json` 和
`build/modular-minimal-final/firmware-verification.json`。覆盖 DDR、UART、IRQ、
Flash 只读、SD 原生块读取/文件 CRC、LCD 帧缓冲、SPI LCD、USB 枚举/重启、
PHY reset、Ethernet PHY/MDIO/解析器、静音音频 DMA、单路及双路麦克风快照，
以及未启用功能的 `UNSUPPORTED`。本轮网口无链路，未重新验证外部 UDP；
没有重做人工按键/屏幕观察/听音验收，也没有 SD/Flash 写入测试。

结束时已用新 ABI 的 `build/modular-stereo-app/firmware/app.img` 恢复双麦克风
波形 demo，额外的 `test mic stereo` 与 64 帧 `perf` 都通过；最终麦克风
overruns、USB errors/drops、LCD underflows 均为 0，见
`build/modular-full-good/stereo-demo-verification.json`。下载方式为 FPGA SRAM
与 UART，Flash 的启动配置未改写。配置矩阵不等于 29 种配置全部经过上板验证。

2026-10-02 构建目录清理后，保留上述全功能/最小 FPGA 构建、对应最小 monitor
和双麦克风应用、`build/m10-release` Flash 基线，以及最终配置矩阵报告。
旧构建的报告、日志及采集证据统一移到 `build/evidence/`，原始相对路径保留；
例如旧的 `build/m9/validation.json` 现在位于
`build/evidence/m9/validation.json`。路径映射及 SHA256 见
`build/cleanup-report.json`；历史报告引用的旧 bitstream 已删除，不能用于下载。
