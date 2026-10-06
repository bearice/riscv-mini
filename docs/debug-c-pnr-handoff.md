# RISC-V C 扩展 PnR 调试纪要

当前可用配置为 `build/c-fit/ddr-depth1-p2`：原生 C+MMU+FPU CPU、全部外设、1 项 DDR bank command queue、Gowin place 2/route 2。PnR setup/hold 0/0，音频 13/13、DMA 和 60 秒综合压力测试通过，详细身份和验收见末节。CPU 外部 fence/atomic 接口保持删除，BIOS 与原失败版本逐字节一致；没有更新 Flash。旧 `no-fence` bitstream 的布局敏感故障尚未定位到单个门级路径，独立 127-cycle ACK 模型下的 FENCE.I 失败也未关闭。下面保留调查过程和失败候选的证据。

## 当前代码状态

- 默认 CPU 仍为无 C 的 `VexRiscv_MmuFpu.v`，默认 ROM 仍为 8 KiB。
- `scripts/cpu_generate.py` 增加了 `--compressed`，可以生成四种 MMU/FPU 组合的 C 版本。
- `scripts/cpu_generate.py` 增加了 `--pipelined-fetch`。它把 C 版本的 `twoCycleCache` 恢复为 `true`，并打开 `injectorStage`，用于缩短取指关键路径。
- `scripts/build.py` 和 `firmware/bootloader/boot.ld` 支持 `--rom-size 4096/8192`；默认值仍为 8192。
- `scripts/test_cpu_fence.py` 支持 `--compressed`，`tests/cpu_fence_program.S` 增加了跨 cache line 的压缩指令测试。
- CPU 外部 fence/atomic 补丁、L2 atomic bypass 和未使用的 posted write combiner 已删除；原生 CPU 的指令语义由 ACK-visible RTL 测试检查，旧候选仍保留在各自输出目录。
- DDR 默认使用 1 项注册命令队列，关闭 auto precharge；`tests/ddr_queue_test.py` 默认验证生产深度，`tests/ddr_queue_structure_test.py` 检查实际 controller/crossbar 的组合反馈环。
- 默认 Gowin place 2/route 2；实验日志、生成的 CPU RTL 和 bitstream 留在忽略的 `build/c-fit` 目录，失败候选不能下载。发布和验收须核对具体产物身份。

## 复现环境

工作目录：`C:/Users/bearice/Workspace/TangPrimer-20K/riscv-mini`

工具路径：

```powershell
$py = '.venv/Scripts/python.exe'
$gcc = 'C:/xpack-riscv-none-elf-gcc-15.2.0-1/bin/riscv-none-elf-gcc.exe'
$iverilog = 'C:/msys64/ucrt64/bin/iverilog.exe'
$java = 'build/opensbi-jdk/jdk8u504-b01/bin/java.exe'
$sbt = 'build/cpu-qualification/sbt-launch.jar'
$vex = 'build/opensbi-cpu-source'
```

生成带 C、取指流水线的原生 CPU（没有外部 fence/atomic 补丁）：

```powershell
.venv/Scripts/python.exe -X utf8 scripts/cpu_generate.py `
  --vexriscv-source build/opensbi-cpu-source `
  --java build/opensbi-jdk/jdk8u504-b01/bin/java.exe `
  --sbt-launch build/cpu-qualification/sbt-launch.jar `
  --output-dir build/c-fit/cpu-native `
  --compressed --pipelined-fetch
```

构建 full、MMU+FPU、SD lite、4 KiB boot ROM 并运行 Gowin PnR：

```powershell
.venv/Scripts/python.exe -X utf8 scripts/build.py `
  --cpu-rtl-dir build/c-fit/cpu-native `
  --rom-size 4096 `
  --output-dir build/c-fit/<candidate> `
  --synthesize
```

`<candidate>` 需要替换成新的目录；不要覆盖已有报告。默认 build profile 包含 MMU、FPU、full 外设、SD lite、DDS 音频、RGB LCD、USB、Ethernet、麦克风和 WS2812。

## 已通过的快速检查

配置和 DDR CSR 协议：

```powershell
.venv/Scripts/python.exe -X utf8 tests/config_test.py
.venv/Scripts/python.exe -X utf8 tests/ddr_boot_test.py
```

带 C 的 RTL fence/取指测试：

```powershell
.venv/Scripts/python.exe -X utf8 scripts/test_cpu_fence.py `
  --cpu build/c-fit/cpu-native/VexRiscv_MmuFpu.v `
  --output-dir build/c-fit/native-fence-test `
  --iverilog C:/msys64/ucrt64/bin/iverilog.exe `
  --compressed
```

原生 CPU 测试结果为 `CPU RTL fence PASS`，覆盖压缩指令、跨 cache line 的 32 位指令、自修改代码/FENCE.I、AMO、LR/SC 和 MMIO 可见性。测试模型在 Wishbone ACK 时提交写入；不再模拟外部 fence 延迟完成。`--ack-delay` 可改变数据总线等待周期；这不是完整内存模型 compliance。反汇编可从 `build/c-fit/native-fence-test/program.elf` 查看；`compressed_fetch` 中 15 个 `c.nop` 后紧跟一个非压缩 32 位 `addi`，它从 cache line 的最后半字开始。

DDR 队列仿真：

```powershell
.venv/Scripts/python.exe -X utf8 tests/ddr_queue_test.py
.venv/Scripts/python.exe -X utf8 tests/ddr_queue_test.py --depth 0
```

深度 8、1、0 都通过 48 个地址、bank/row 切换、16 字节 mask、原生读写数据时序和刷新后的读回。日志在 `build/c-fit/ddr-queue-test.log` 和 `build/c-fit/ddr-queue0-test.log`。

## PnR 实验矩阵

所有候选使用 60 MHz CPU/Wishbone、120 MHz DDR CK、4 KiB L2、4 KiB boot ROM、full 外设和 Gowin place option 3/route option 2，除非另有说明。

| 候选 | 结果 | 关键证据 |
| --- | --- | --- |
| C，无取指流水线 | 226 条未布通网络 | `build/c-boot/full-4k/gateware/impl/pnr/project.rpt.txt` |
| C + `--pipelined-fetch`，默认 USB 相位 225° | 资源约 Logic 19550、Register 11092、CLS 10240、BSRAM 42；9 个 setup、0 个 hold，USB DIR 路径最差约 −0.789 ns | `build/c-fit/queue0/gateware/impl/pnr/project_tr_content.html` |
| 同上，place option 1 | 12 个 setup、0 个 hold，最差约 −1.165 ns | `build/c-fit/queue0-p1/gateware/impl/pnr/project_tr_content.html` |
| USB ULPI 相位 247.5° | USB 路径改善，但 DDR DQS 跨时钟路径产生 8 个 setup、0 个 hold，最差约 −0.413 ns | `build/c-fit/phase247/gateware/impl/pnr/project_tr_content.html` |
| 裁剪前 `c-fit-v4`，done 恒为 1、L2 注册命令、USB 247.5°、DDR depth 0 | Logic 19730、LUT 16409、Register 11093、CLS 10220；136 条未布通网络 | `build/c-fit/c-fit-v4/gateware/impl/pnr/project.rpt.txt` |
| 原生 CPU，删除 fence/atomic 补丁和 L2 bypass | 全部布通，setup/hold 0/0；Logic 19517、LUT 16198、Register 11075、CLS 10264、BSRAM 42 | `build/c-fit/no-fence/validation.json` |

取指流水线之后，CPU 取指 PC 到寄存器堆的原始 −6 ns 级关键路径消失；剩余瓶颈先后转移到 USB ULPI 输入/输出使能、DDR DQS 跨时钟控制、FPU 执行级以及共享总线返回路径。只切换布局参数没有解决问题。

DDR 控制器队列深度实验曾把 bank command FIFO 从默认 8 项缩到 1/0 项。综合层面 depth 1 将 SDRAM 层 LUT 从约 1375 降到 1213，depth 0 降到约 1184。裁剪阶段工作树为 `cmd_buffer_depth=0, with_auto_precharge=False`（后续音频修复配置见末节）、USB ULPI 247.5°；此次裁剪保留这些已有设置，没有恢复上游默认，也没有改变时序约束。`c-fit-v4` 与 `no-fence` 的 CST/SDC 哈希及 Gowin options 相同，所有非 CPU/L2/顶层模块内容相同。移除相关逻辑后 Logic −213、LUT −211、Register −18，但 CLS +44；资源减少不保证布局占用同步减少。

## 关键边界和下一步建议

1. `no-fence` 已获得零 setup/hold 的 full PnR，但不能据此宣称功能验收通过。
2. 不能下载 `build/c-fit/queue0` 或 `build/c-fit/phase247` 的 bitstream；它们的 `validation.json` 会记录 timing violation，`scripts/boot_upload.py` 也会拒绝这类输出。
3. 原生 CPU 在 ACK 延迟 1/9/31 周期时通过压缩指令、自修改代码/FENCE.I、AMO、LR/SC；延迟 127 周期时自修改代码超时。取指在 cycle 2680 读到代码地址的旧值 0，两条代码 store 在 cycle 2717/2844 才 ACK。相同程序、ACK 模型和延迟下，旧补丁 CPU（done=1）在 3450 cycles 通过。日志在 `build/c-fit/native-fence-delay127/{simulation,trace,reference}.log`。
4. 该压力模型有独立 I/D 总线，写入 ACK 时立即可见，已证明这种模型下的功能回归；尚未用完整 SoC 共享 Wishbone/L2 仲裁复现，不能把它直接当成实板故障。此前“flush 提前”断言本身也不能替代最终可见结果检查。
5. 共享 L2（含 dirty 可见性、轮转、混合参考序列）、4/8 KiB boot RAM、L2 生成 Verilog 字节 lane、DDR 软件初始化、配置和 RTL 默认值/复位优先级测试均通过。
6. 已按用户要求下载 SRAM 并执行板上验收，没有更新 Flash。实板 `test fence` 连续通过，但独立慢 ACK 模型的 FENCE.I 回归仍未解决，不能由有限实板测试覆盖该边界。

压力测试复现：

```powershell
.venv/Scripts/python.exe -X utf8 scripts/test_cpu_fence.py `
  --cpu build/c-fit/cpu-native/VexRiscv_MmuFpu.v `
  --output-dir build/c-fit/native-fence-delay127 `
  --iverilog C:/msys64/ucrt64/bin/iverilog.exe --compressed --ack-delay 127
```

## 资源和实验输出

C 版 boot ROM 为 3792 B，旧版无 C boot ROM 为 5292 B；当前结果为 `build/c-fit/no-fence/validation.json`，ISA `rv32imafc_zicsr_zifencei`，镜像 ABI `ba18270e`。资源为 Logic 19517/20736、Register 11075/16173、CLS 10264/10368、BSRAM 42/46、rPLL 3/4。CLS 只剩 104 个，任何新增寄存器或宽 FIFO 都可能改变布局结果。

## 实板验收（2026-10-05）

使用 COM4、Gowin programmer location 107569、2 MHz JTAG 将 `no-fence` 下载到 SRAM，通过 UART 装载匹配 BIOS。bitstream SHA256 为 `0d8664c471f267366d48b2be9bdd3363131e4a6b513e7208f208bc81f386b24f`；BIOS app.img SHA256 为 `72a3d70580420c63f5e59864e44549ad6a820a215453b0f274ca1692e394684a`。

- FPU、Sv32 MMU、C ISA、L2、UART、IRQ、DDR scratch、Flash 只读、SD CRC/多块、LCD CRC/扫描、SPI LCD 传输、IO 寄存器检查通过；LCD underflow 为 0。SPI/LCD 显示、灯光和按键的物理效果没有人工确认。
- `test fence` 首次、后续连续十次及干净重启后一次均 PASS（共 12 次）；这个结果不消除 127-cycle 模型下的已知失败。
- USB HID 枚举、拓扑、stop、三次 restart、共享 PHY reset 通过。接收器为 VID 046d/PID c52b、3 HID interfaces；未验证真实键鼠输入。
- Ethernet PHY/协议解析器和单双麦克风采集通过；外部 Ethernet 测试口未连接，未验证真实网络收发，未听音或验证麦克风声学响应。
- `test audio` 初次、三次重试及干净重启后一次均出现 `TEST audio refill FAIL`（5/5 失败）；`test dma` 的 SD 部分通过、音频部分失败。`test soak 60` 第一轮在约 1.4 秒退出，SD/LCD CRC 和 USB 检查通过，音频 refill 失败。终止后已静音并停止 DMA；根因尚未确定，不能直接归因于 fence 裁剪。
- `scripts/boot_verify.py --reset` 通过坏 header/ABI/地址/长度、packet/payload CRC、UART timeout 的拒绝以及 UART BIOS 重新装载检查，没有请求 Flash install。
- 两次重新下载 SRAM 和三次软件复位均完成 DDR 训练及 BIOS POST；`test bios` 服务检查通过。日志见 `boot-repeat-verification.json`、`board-fresh.json` 及对应 UART 日志。

原始报告保存在候选目录的 `firmware-verification.json`、`board-remaining.json`、`boot-verification.json` 及对应 UART 日志。整体结果为验收未通过，不把 PnR 通过改写为功能通过。

本轮没有修改 Flash。后台防休眠任务已停止。

## 音频故障调查与修复配置（2026-10-06）

当前可用产物为 `build/c-fit/ddr-depth1-p2`。full C+MMU+FPU+全部外设，60/120 MHz、L2/ROM 各 4 KiB；PnR 全部布通、setup/hold 0/0。原版 BIOS 的 `app.bin` 与失败的 `no-fence` 完全相同，app.img SHA256 仍为 `72a3d70580420c63f5e59864e44549ad6a820a215453b0f274ca1692e394684a`。没有恢复 CPU 的外部 fence/atomic 接口。

### 失败链与定位边界

`tests_poll()` 的 `producer - audio.fetched` 偶尔超过 32768，停止 refill 并静音。失败快照的 fetched 为 8；紧接着的直接 CSR 读值正常，实际 ring occupancy 仍小于 32768。后续 underrun 是停止 refill 的后果。

最小 CPU+DDR/L2+audio 系统通过；加入实际 Ethernet info copy 的小程序、100000 次快照/直接读比较、DMA 活动及 IRQ on/off 也通过。对原版 BIOS 做保持地址不变的二进制对照：跳过 Ethernet info 的 memcpy 或把目标寄存器 t1 换成 t2 会消除本次复现，跳过 hal_poll、USB event drain、timer IRQ 不会。CPU RTL 在原版 memcpy/get_info、中断、独立/共享 I/D 仲裁、DDR 地址区取指、空闲 MISO 变化条件下均通过；这不能替代 FPGA 的实际实现验证。

关键证据：在原失败 bitstream 上，把 `0x40806028` 的 `lw t1,48(a5)` 原地替换成 `lui t1,0x8`，期望固定写入 0x8000，仍有一次快照得到 8。日志 `build/c-fit/audio-constant-exact/audio-diag-uart.log`：producer=0x10000、snapshot=8、direct=0xc58d。因此 CSR fetched 读操作不是错误的必要条件；目前仍不能仅凭软件快照区分 CPU 执行/寄存器读写和缓存/内存 store 可见性的具体故障位置。

### 修复及对照

LiteDRAM depth 0 的 lookahead FIFO 是组合直通，而 `req.lock = lookahead.valid | buffer.valid`。crossbar 又用其它 bank 的 lock 抑制本 bank 的 valid，形成跨 bank 的 `lock -> valid -> lock` 组合环。旧综合报告包含 `AG0100 Find logical loop`，零违例 STA 并不能使这个组合结构成立。DFI 行为仿真会把反馈环迭代到稳定值，旧的 depth 0 仿真 PASS 因而不足以确认硬件可用。

1. `gateware/ddr_boot.py` 使用 `DDR_CMD_BUFFER_DEPTH=1`，保留最浅注册队列，保持 `with_auto_precharge=False`。实际 controller/crossbar 的组合依赖检查显示 depth 0 有环、depth 1/8 无环；新的 `tests/ddr_queue_structure_test.py --depth 0` 会明确失败，默认生产配置通过。
2. `scripts/build.py` 和 SoC 默认 Gowin 参数改为 place 2/route 2。depth 1/place 3 有 128 setup、0 hold 违例，未下载；depth 1/place 2 零违例。没有修改时序例外来隐藏失败。
3. **不可把音频故障唯一归因于 DDR 环。** 同样的 depth 0/place 2 对照也零违例并通过音频 10/10；增加小型硬件采样器后的 depth 0/place 3 也通过。已确认旧失败 bitstream 对硬件布局敏感，已消除一个独立且明确的组合环缺陷；旧 bitstream 内部哪一个门级路径产生错误仍未唯一定位。

| 配置 | PnR | 音频实板 |
| --- | --- | --- |
| 原 no-fence，depth 0/place 3 | setup/hold 0/0 | 原验收 5/5 FAIL，本次重载仍 2/2 FAIL |
| depth 0/place 2 对照 | 0/0 | 10/10 PASS |
| depth 1/place 3 | 128/0 | 未下载 |
| 最终 depth 1/place 2 | 0/0 | 13/13 PASS，DMA PASS，soak 60 PASS |

### 最终验收与资源

- `test audio` 13 次通过，包含重新下载 SRAM 后 3 次；`test dma` 通过。
- `test soak 60` 41 轮，elapsed_ms=0xee18，音频 played=0x2ca4e4、fetched=0x2ca6e4；DMA 阶段 underrun/overrun/error 全部为 0，LCD underflow 0，SD/LCD CRC 和 USB 持续检查通过。PIO 阶段在未持续供样时的计数不是 DMA 阶段的失败统计。
- CPU C/FPU/Sv32、L2/fence、DDR scratch、UART/IRQ、Flash 只读、SD CRC/多块、LCD、SPI LCD、IO、USB HID、Ethernet PHY/寄存器、MIC 数据和 BIOS 服务共 35 项命令检查通过。没有做人耳音质、真实网络收发或视觉/按键效果验收。
- `boot_verify.py --reset` 的 header/ABI/地址/长度、packet/payload CRC、timeout 拒绝、UART 重载和复位检查通过；本轮始终只写 FPGA SRAM 和 UART RAM，没有写 Flash。
- 临时硬件采样器和 firmware debug 改动已清理。`audio-fix-repro` 使用当前默认参数重新生成，所有 gateware/modules、顶层 RTL 和 app.bin 与已验收的 depth1-p2 逐字节一致。
- Logic 19797/20736，LUT 16473，Register 11251/16173，CLS 10295/10368，BSRAM 42/46，PLL 3/4。相对 no-fence 增加 LUT 275、Register 176、CLS 31；恢复注册 DDR 队列会付出资源代价。当前 CLS 余量 73。
- bitstream SHA256 `fcb9ccc9c6abc3c0ff0df8c1121e1d0fd9816de49c8f1703d4b4ee05bb0517a4`。详细日志：`audio-fix.json`、`audio-fix-uart-reconstructed.log`、`boot-verification.json`、`boot-verification-uart.log`；另有最终重新下载后的 `boot-upload-uart.log`。

复现构建：

```powershell
.venv/Scripts/python.exe scripts/build.py --output-dir build/c-fit/ddr-depth1-p2 `
  --cpu-variant linux --cpu-verilog build/c-fit/cpu-native/VexRiscv_MmuFpu.v `
  --with-mmu --with-fpu --rom-size 4096 --l2-size 4096 `
  --usb-backend ultra --sd-profile lite --audio-clock dds --synthesize
.venv/Scripts/python.exe tests/ddr_queue_structure_test.py
.venv/Scripts/python.exe tests/ddr_queue_test.py
```

原独立 I/D 总线 127-cycle ACK 压力模型的 FENCE.I 失败仍是另一项未关闭的边界；本轮音频/DMA 修复配置通过实板测试不等于已修复该模型。
