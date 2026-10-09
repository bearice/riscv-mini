# 模块化构建

默认构建生成唯一的 `build/runs/<时间>-<Git版本>-<配置>-<用途>`，不再覆盖固定目录。
查询当前/基线/最新构建、最终产物归档和中间目录清理见 [构建目录说明](build-artifacts.md)；提交流程见 [SOP](pre-commit-sop.md)。

默认 full 构建启用全部现有外设、MMU 和 SD lite，FPU 默认关闭，并生成实际的 Verilog 模块层级。CPU（VexRiscv）、DDR、UART、8 KiB boot ROM、计时器/IRQ 和系统控制器
构成固定内核：当前启动协议要求把应用装入 DDR，ROM 在 L2 启动 RAM 上完成 DDR 初始化/训练，因此这些不作为可关闭外设，也没有集成 SRAM。
CPU/sys 60 MHz、DDR 120 MHz 不变。
`--rom-size 4096/8192` 选择 ROM 地址窗口，默认仍为 8192。链接器、硬件映射和构建报告使用相同容量，并拒绝固件超出窗口；压缩指令通过 CPU 生成器的 `--compressed` 及能力元数据选择，见 [CPU](cpu-mmu-fpu.md)。启动栈仍使用固定 4 KiB L2 窗口，不随 ROM 容量变化。
默认使用 4 KiB shared writeback L2；`--l2-size 4096/8192` 控制容量。CPU/音频使用 32-bit Wishbone，LCD 使用 16-bit、SD lite 使用 32-bit 端口，内存控制器缓冲后进入 128-bit coherent 入口。构建不再提供旧的缓存策略、写合并、posted write 或 scheduler 实验开关。原理与维护见 [L2 缓存](l2-cache.md)，客户端路径见 [DMA](native-dma.md)。
默认 Gowin `place_option=2`、`route_option=2`、`netlist_hierarchy=0`，可用
`--place-option` / `--route-option` 覆盖。当前配置和资源见 [系统设计](system-design.md)。

## 功能开关

每个功能同时接受 `--with-NAME` 和 `--without-NAME`。`--profile full` 是
默认值；`--profile minimal` 仅保留内核和 Flash。关闭 Flash 后为 UART-only
装载，仍然检查镜像 ABI/CRC，不能安装或自动加载 Flash 应用。

| NAME | 功能 | 依赖 |
| --- | --- | --- |
| `mmu` | Sv32、M/S/U；full 默认开启 | 匹配生成的 CPU RTL |
| `fpu` | 单精度 FPU；默认关闭，显式 `--with-fpu` 启用，与 MMU 独立 | 匹配生成的 CPU RTL |
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
MMU 默认开启、FPU 默认关闭；minimal 默认关闭两者，仍可分别覆盖。音频默认平均 48 kHz 的共用 DDS，保留旧整数分频作对照。

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
块内的 FSM、FIFO、CSR 实现内联在所属模块中；实际模块清单由每次构建生成，
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
`filesystem/video/usb/audio` 都启用。bootloader 链接 UART、计时器、DDR 初始化和可选 Flash，不链接外设 HAL。

配置标志参与镜像 ABI，即使关闭 FatFs 或第二只麦克风没有改变 CSR 地址，
应用也不能误装到另一个配置。`validation.json` 保存最终功能列表、时钟、
RTL 模式/manifest、固件大小、ABI，以及请求综合时的资源与时序结果。

## 验证方法

```powershell
# 53 个功能开关、CPU×SD 和音频组合，无上板操作。
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
