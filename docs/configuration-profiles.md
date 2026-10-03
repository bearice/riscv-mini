# CPU、SD 和音频配置

CPU/总线保持 60 MHz，DDR CK 保持 120 MHz。2026-10-03 起，默认 `--profile full` 选择全部外设、MMU+FPU、SD lite、音频 DDS。MMU 和 FPU 仍可独立关闭；`--profile minimal` 默认不启用 MMU/FPU。
默认输出 SoC 块模块层级，Gowin place=3 / route=2；完整当前实板验收见
[full 验收](full-validation-2026-10-03.md)。下面的历史资源对照保留其原始配置。

| CPU 参数 | 能力 | 外部 RTL |
| --- | --- | --- |
| `--without-mmu --without-fpu` | lite，RV32IM | 无需额外生成 |
| `--without-mmu --with-fpu` | 单精度 FPU、2 KiB I/D cache | VexRiscv_Fpu.v |
| `--with-mmu --without-fpu` | Sv32、M/S/U、2 KiB I/D cache | VexRiscv_Mmu.v |
| `--with-mmu --with-fpu`（full 默认） | Sv32、M/S/U、单精度 FPU、2 KiB I/D cache | VexRiscv_MmuFpu.v |

裸机应用使用 ilp32 ABI；FPU 构建启用 `af` 指令扩展。bootloader 按实际能力分别初始化 satp、浮点状态和 D-cache，不把 MMU/FPU 绑定在一起。显式 `--cpu-verilog` 会检查 RTL 中的实际 MMU/FPU 能力；与显式 flag 不一致时拒绝构建。MMU 支持并不意味着已有内核、进程隔离或 RTOS。

| `--sd-profile` | 控制器 | DMA / 文件系统 |
| --- | --- | --- |
| `none` | 无 SD | 同时移除 FatFs |
| `spi` | SPI SD，工作 6 MHz | 软件 SPI 读写，可选 FatFs |
| `lite` | 精简原生四位，工作 7.5 MHz | 最多 8 扇区/4 KiB 一次 DMA，可选 FatFs |
| `full` | 通用原生四位，工作 7.5 MHz | 保留更宽地址/长度和通用 DMA，可选 FatFs |

full 的 HAL 当前仍以最多 8 扇区分块；更宽硬件不代表 HAL 已改成大块传输。显式 SD profile 会在 minimal 配置中启用 SD；`none` 与显式启用文件系统冲突时拒绝构建。旧 `--sd-backend native/spi` 仍作为兼容入口，不能与 `--sd-profile` 同时指定。bootloader 仍只含 Flash/UART，不含 SD 或显示驱动。

音频默认 `--audio-clock dds`，DAC 与两路麦克风共用一个相位累加器，平均采样率 48 kHz，不新增 PLL。步进根据构建时系统频率计算；改变频率后必须重新构建，不支持运行时动态调频。边沿量化与 legacy 对比见 [时钟树](clocks.md)。

首次使用 MMU/FPU 先生成四种 RTL：

```powershell
.venv/Scripts/python.exe scripts/cpu_generate.py --vexriscv-source build/cpu-qualification/generator/ext/VexRiscv --java build/cpu-qualification/tools/jdk8u504-b01/bin/java.exe --sbt-launch build/cpu-qualification/sbt-launch.jar --output-dir build/cpu-features
.venv/Scripts/python.exe scripts/build.py --with-mmu --with-fpu --sd-profile lite --audio-clock dds --flat-verilog --synthesize --output-dir build/config-profiles/mmu-fpu-lite-dds
.venv/Scripts/python.exe scripts/build_matrix.py --configuration-only --jobs 2 --output-dir build/config-matrix-dds
```

默认仍输出分模块 Verilog。2026-10-02 的上板候选使用 `--flat-verilog`。2026-10-03 的分模块启动诊断及修复验收见 [RTL 默认值诊断](rtl-defaults.md)。

本轮仿真检查真实 DAC 引脚的 PCM 顺序、静音和欠载，以及麦克风 I2S 延迟位、PCM24 符号、双通道快照与停止。DDS 参数测试覆盖 48/60/120 MHz，检查 48 kHz 帧周期和共用相位。CPU×SD 的 16 种组合和四种音频组合只做 RTL 生成与固件编译；它们的结果见 `build/config-matrix-dds/result.json`，不构成全部组合上板验收。整机 PnR 与上板结果另记，Flash 未更新。

## 2026-10-02 flat 对照版本

`build/config-profiles/mmu-fpu-lite-dds/validation.json`：全部外设、MMU+FPU、SD lite、DDS；flat RTL。setup/hold 违例均为 0。

| 资源 | 使用 / 总数 |
| --- | --- |
| Logic | 18,608 / 20,736 |
| Register | 10,776 / 16,173 |
| CLS | 10,033 / 10,368 |
| BSRAM | 43 / 46 |
| rPLL | 3 / 4 |
| PRIMARY / LW | 8 / 8；8 / 8 |

PLL 留有一颗；CLS 和全局时钟布线资源仍接近上限，不能仅根据 PLL 数量判断后续扩展是否能布通。boot 二进制 6,584 B，DDR 应用 69,804 B。

FS SHA256：`60552afad083f21b9b457aeb88a5f54bdf1a4628f7873d77c8274b690a748c8a`；ABI `0xadf18467`。原始路由及资源报告在该目录的 `gateware/impl/pnr/`。

分模块版本 `build/config-profiles/mmu-fpu-lite-dds-hierarchical/validation.json` 编译通过，输出 262 个模块，未做本轮 PnR 或下载。

两个音频 PLL 对照版本均未下载：`mmu-fpu-lite-pll` 完成路由但有 8 setup / 2 hold 违例；`mmu-fpu-lite-pll48` 有 758 条未布通网络。单独的 `clock-probe`（120→307.2→61.44→12.288 MHz）时序 0/0，但用户最终选择 DDS；不能用时钟探针通过来推断整机通过。全部实验路径和 CPU RTL 哈希汇总见 `build/config-profiles/configuration-evidence.json`。

## 上板验收

该 DDS 候选已下载至 FPGA SRAM，应用通过 UART 装入 DDR。`firmware-verification.json` 与 `firmware-verification-uart.log` 位于同一构建目录：37 种命令通过，包括 FPU、Sv32、DDR、原生 SD 块/文件读写、显示、USB 重启/枚举、PHY/MAC/parser、静音音频 DMA 和双麦克风快照。SD 新建文件 `RVT00005.BIN`；两路麦克风均有非零数据。

`test soak 300` 通过。最终显示欠载、音频欠载/错误、SD 错误、USB 错误均为 0；用户确认两块 LCD 正常稳定。测试后停显示扫描及音频/网络测试负载，回到 monitor，取消防休眠进程。

本轮没有听音、重新操作键鼠、拔插验收或外部 UDP 收发；最终 status 的 Ethernet link 为 down。没有更新 Flash，也没有执行断电检查。其余 CPU×SD 组合只编译通过，不能套用本配置的上板结论。
