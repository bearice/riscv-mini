# CPU、SD 和音频配置

Ethernet ring DMA 通过 `--feature eth_dma=on` 启用，默认关闭，依赖 `eth`。DMA 构建使用 native DDR descriptor ring，移除 CPU packet SRAM 和旧 PIO 命令映射；HAL/U-Boot 使用相同的独占 ring ABI。无 FPU、全音频/麦克风组合的验收见 [Ethernet ring](ethernet-ring-exclusive.md)。

CPU/总线保持 60 MHz，DDR CK 保持 120 MHz。2026-10-09 起，默认 `--profile full` 选择全部外设、MMU、无 FPU、SD lite、音频 DDS。MMU 和 FPU 仍可独立关闭；`--profile minimal` 默认不启用 MMU/FPU。
默认内存路径为 4 KiB shared writeback L2，CPU/音频通过 32-bit Wishbone，LCD 16-bit / SD lite 32-bit 端口经内存侧 buffer 进入 128-bit coherent 入口，后端直连 LiteDRAM crossbar；CPU 使用原生 cache/fence 实现，不导出额外 fence/atomic 信号。
默认输出 SoC 块模块层级，Gowin place=2 / route=2，DDR bank command queue 深度为 1；完整当前实板验收见
[系统设计](system-design.md)。当前资源见该页。

| CPU 参数 | 能力 | 外部 RTL |
| --- | --- | --- |
| `--feature mmu=off --feature fpu=off` | lite，RV32IM | 无需额外生成 |
| `--feature mmu=off --feature fpu=on` | 单精度 FPU、2 KiB I/D cache | VexRiscv_Fpu.v |
| `--feature mmu=on --feature fpu=off`（full 默认） | Sv32、M/S/U、2 KiB I/D cache | VexRiscv_Mmu.v |
| `--feature mmu=on --feature fpu=on` | Sv32、M/S/U、单精度 FPU、2 KiB I/D cache | VexRiscv_MmuFpu.v |

裸机应用使用 ilp32 ABI；FPU 构建启用 `af` 指令扩展。bootloader 按实际能力分别初始化 satp、浮点状态和 D-cache，不把 MMU/FPU 绑定在一起。显式 `--cpu-verilog` 会检查 RTL 中的实际 MMU/FPU 能力；与显式 flag 不一致时拒绝构建。MMU 支持并不意味着已有内核、进程隔离或 RTOS。

| `--sd-profile` | 控制器 | DMA / 文件系统 |
| --- | --- | --- |
| `none` | 无 SD | 同时移除 FatFs |
| `spi` | SPI SD，工作 6 MHz | 软件 SPI 读写，可选 FatFs |
| `lite` | 精简原生四位，读 15 MHz / 写 7.5 MHz | 最多 8 扇区/4 KiB 一次，32-bit DMA 端口 / 内存侧 128-bit burst，可选 FatFs |
| `full` | 通用原生四位，读 15 MHz / 写 7.5 MHz | 保留更宽地址/长度和通用 DMA，可选 FatFs |

full 的 HAL 当前仍以最多 8 扇区分块；更宽硬件不代表 HAL 已改成大块传输。显式 SD profile 会在 minimal 配置中启用 SD；`none` 与显式启用文件系统冲突时拒绝构建。SD 后端统一用 `--sd-profile` 选择，不再接受旧后端别名。bootloader 只含 DDR 初始化和 Flash/UART，不含 SD 或显示驱动。

音频默认 `--audio-clock dds`，DAC 与两路麦克风共用一个相位累加器，平均采样率 48 kHz，不新增 PLL。步进根据构建时系统频率计算；改变频率后必须重新构建，不支持运行时动态调频。边沿量化与 legacy 对比见 [时钟树](clocks.md)。

生产使用的 MMU/无 FPU 核已提交在 [`gateware/vendor/vexriscv/`](../gateware/vendor/vexriscv/)（`VexRiscv_Mmu.v` + `generator.json` + `PROVENANCE.json` + MIT 许可），`full` 配置不再需要传 `--cpu-verilog`。生成参数（寄存器配置、cache 大小、single-cycle 移位/乘除、取指流水线标志）、生成器与上游 checkout 的哈希、Java/sbt 身份都记录在 `generator.json` 的 `commands` / `resolved_flags` / `fixed_parameters` / `source_lock` 中；`scripts/modular/machines/primer20k_vexriscv/cpu.py:locked_cpu` 在每次构建前校验 sha256 与参数一致性，文件缺失或被改动就直接报错。

需要其它能力组合（FPU、压缩指令、不同取指流水线）时在本地生成实验核，不覆盖已提交的生产核：

```powershell
.venv/Scripts/python.exe scripts/mini.py build cpu --generate --source <VexRiscv 源码目录> --java <JDK8>/bin/java.exe --sbt-launch <sbt-launch.jar> --output-dir build/cpu-features
.venv/Scripts/python.exe scripts/mini.py build system --cpu-verilog build/cpu-features/VexRiscv_MmuFpu.v --feature mmu=on --feature fpu=on --sd-profile lite --audio-clock dds --synthesize --output-dir build/config-profiles/mmu-fpu-lite-dds
.venv/Scripts/python.exe scripts/mini.py build matrix --configuration-only --cpu-rtl-dir build/cpu-features --output-dir build/config-matrix
```

调整生产核参数时，改完生成器参数后用 `--vendor-dir` 重新生成并入库；工具在重新生成的字节与已提交文件不一致时会拒绝覆盖（确认接受则加 `--force`），随后同步更新 `generator.json` 与 `PROVENANCE.json` 的哈希：

```powershell
.venv/Scripts/python.exe scripts/mini.py build cpu --generate --source <VexRiscv 源码目录> --java <JDK8>/bin/java.exe --sbt-launch <sbt-launch.jar> --vendor-dir gateware/vendor/vexriscv
```

默认输出分模块 Verilog，转换规则见 [RTL 模块边界](rtl-defaults.md)。

仿真检查真实 DAC 引脚的 PCM 顺序、静音和欠载，以及麦克风 I2S 延迟位、PCM24 符号、双通道快照与停止。DDS 参数测试覆盖 48/60/120 MHz，检查 48 kHz 帧周期和共用相位。CPU×SD 的 16 种组合和四种音频组合只做 RTL 生成与固件编译，结果写入矩阵输出目录的 `result.json`（构建产物，不入库），不构成全部组合上板验收。
