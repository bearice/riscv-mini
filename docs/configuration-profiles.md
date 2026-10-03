# CPU、SD 和音频配置

CPU/总线保持 60 MHz，DDR CK 保持 120 MHz。2026-10-03 起，默认 `--profile full` 选择全部外设、MMU+FPU、SD lite、音频 DDS。MMU 和 FPU 仍可独立关闭；`--profile minimal` 默认不启用 MMU/FPU。
默认输出 SoC 块模块层级，Gowin place=3 / route=2；完整当前实板验收见
[系统设计](system-design.md)。当前资源见该页。

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

首次使用 MMU/FPU 先生成四种 RTL（需要 Java 8、sbt-launch 1.9.7 与兼容的 VexRiscv checkout，固定版本为 `b6118e5cc2a33323425df6455697139021d50c72`）：

```powershell
.venv/Scripts/python.exe scripts/cpu_generate.py --vexriscv-source <VexRiscv 源码目录> --java <JDK8>/bin/java.exe --sbt-launch <sbt-launch.jar> --output-dir build/cpu-features
.venv/Scripts/python.exe scripts/build.py --with-mmu --with-fpu --sd-profile lite --audio-clock dds --synthesize --output-dir build/config-profiles/mmu-fpu-lite-dds
.venv/Scripts/python.exe scripts/build_matrix.py --configuration-only --jobs 2 --output-dir build/config-matrix
```

默认输出分模块 Verilog，转换规则见 [RTL 模块边界](rtl-defaults.md)。

仿真检查真实 DAC 引脚的 PCM 顺序、静音和欠载，以及麦克风 I2S 延迟位、PCM24 符号、双通道快照与停止。DDS 参数测试覆盖 48/60/120 MHz，检查 48 kHz 帧周期和共用相位。CPU×SD 的 16 种组合和四种音频组合只做 RTL 生成与固件编译，结果写入矩阵输出目录的 `result.json`（构建产物，不入库），不构成全部组合上板验收。
