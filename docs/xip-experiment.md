# 无 C 扩展、无片内 ROM、Flash XIP 启动实验

基于 `da9a3d8`，分支 `experiment/noc-flash-xip-build`。

当前布局为 RAM 从 0 开始、设备 MMIO 位于 `0xF0000000` 以上、Flash XIP 位于 `0xF3000000`，VRAM 由 CSR 指定 DDR 地址，不增加布局 flag。启动区从物理 Flash 1 MiB 开始；保留取指 injector，使用 `place_option=3 / route_option=2`。full PnR setup/hold 为 0/0；实板完整 BIOS 命令、双麦克风、60 秒 soak，以及 OpenSBI/U-Boot 验收通过。历史试验与各镜像身份见下文。

CPU 为当前生成器重新生成的 RV32IMAF，MMU/FPU 与 2 KiB I/D cache 保留，C 关闭。
full 外设配置、60/120 MHz 时钟与 4 KiB shared writeback L2 保留。

启动路径为 CPU reset → Flash 上的启动程序 → DDR 初始化 → UART 或 Flash 装载 BIOS → DDR BIOS。
启动程序直接在 Flash 上运行；BIOS 本身仍运行于 DDR。
片内 ROM 为 0，启动程序的数据和栈使用 `0x007FF000` 的 4 KiB pinned L2 boot RAM。

| 参数 | 值 |
| --- | --- |
| Flash 映射 | `0xF3000000`，4 MiB，只读、可缓存 |
| CPU reset / boot ELF entry | `0xF3100000` |
| 启动代码物理 Flash offset | `0x100000` |
| 启动区预留 | 1 MiB，即物理 `[0x100000, 0x200000)` |
| XIP SPI | mode 0，10 MHz，`03h` + 24-bit address + 32-bit data |
| NOR 唤醒 | reset 后发送 `ABh`，等待 3 us |
| BIOS 镜像区域 | `0x200000`，原有镜像格式、ABI/CRC 检查 |

硬件 XIP 与原软件 SPI 共享引脚。软件事务 CS 有效时 XIP 等待；DDR BIOS 的 Flash
驱动在选中 CS 前等待 XIP busy 清除。启动程序只走映射读取，不操作软件 SPI，避免
正在执行 Flash 代码时占住软件 CS；启动菜单的 Flash 安装功能在 XIP 模式下拒绝写入。
DDR BIOS 的软件 Flash 驱动继续保留。执行 XIP 代码期间不允许另行发起软件 SPI 事务。

## 复现

在本 worktree 根目录执行，Python 使用主仓库的 `.venv/Scripts/python.exe`：

```powershell
python scripts/cpu_generate.py --vexriscv-source <VexRiscv源码> --java <Java8> --sbt-launch <sbt-launch-1.9.7.jar> --pipelined-fetch --output-dir build/cpu-noc
python scripts/build.py --profile full --cpu-rtl-dir build/cpu-noc --without-compressed --boot-mode xip --place-option 3 --route-option 2 --purpose noc-xip-p3-r2 --synthesize
python tests/flash_xip_test.py
python scripts/test_cpu_fence.py --cpu build/cpu-noc/VexRiscv_MmuFpu.v --iverilog <iverilog> --output-dir build/cpu-noc-fence-test
```

`--without-compressed` 校验输入 RTL 元数据；真正关闭 C 由 CPU 生成器完成。
`firmware/xip.bin` 与 `boot.bin` 内容相同，但链接地址为 XIP 地址，不能用原 UART
boot upload 的 app/install 模式替代 XIP 启动代码安装。`app.img` 是另一个独立产物。

## 前两轮验证记录

第二轮候选：`build/runs/20261007T095843167053Z-da9a3d8-dirty-full-rv32imaf-rom0k-l24k-noc-xip-pipelined/`。
第二轮保留当前 CPU 的 two-cycle I-cache 和 injector register，仅关闭 C。
启动程序 3712 bytes，BIOS binary 104552 bytes。
生成的 memory map 没有 `rom`，没有 ROM init 文件，CPU reset vector 为 `0x20180000`。

- SPI 引脚波形、命令/地址、字节序、连续读取、拒绝写事务、软件 CS 占用等待：PASS。
- 真实无 C CPU RTL 的 fence、FENCE.I、AMO、LR/SC、MMIO：PASS。
- pinned L2 boot RAM 隔离、byte lanes、dirty handoff、reset：PASS。
- DDR 初始化命令、fresh capture、lane comparison、exclusive handover：PASS。
- CPU + XIP reader + NOR 模型联跑：PASS；从 Flash reset vector 取指并执行，确认 misa.C=0，执行 fence/FENCE.I，写出正确 MMIO 签名；第二轮 9600 cycles，24 次 SPI 读取（首轮 9598 cycles）。
  联跑脚本及日志在 `build/cpu_xip_probe.py` 和 `build/cpu-xip-probe-pipelined/simulation.log`。
- 原 ROM 模式 minimal 固件与 RTL 生成：PASS。
- 前两轮 full PnR：均失败，Gowin `PR0004`，未生成可验收 bitstream。
- 实板 Flash 启动、DDR training、外设与性能：未执行。

最早使用历史 v0.6.0 CPU 的试构建已中止；该 CPU 带当前 SoC 不再连接的 external fence
接口，因此不作为可下载候选。最终候选使用当前生成器生成的 CPU。

此前 1.5 MiB 地址的候选未上板；用户随后指定使用 1 MiB 起始空间，完成下述新地址板测。
本节描述迁移 RAM 地址前的历史候选，未固定为 current；最终提交快照由构建 catalog 单独登记。

## PnR 结果

| 配置 | Logic | Register | CLS | BSRAM | 未布通网络 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 首轮，无 injector | 19484 | 11276 | 10238 | 40 | 172 |
| 第二轮，保留 injector | 19428 | 11308 | 10245 | 40 | 46 |
| refs/current 的 v0.7.1，C + 4 KiB ROM | 19797 | 11251 | 10295 | 42 | 0 |

设备容量：Logic 20736、Register 16173、CLS 10368、BSRAM 46。
对照来自主仓库 `build/releases/v0.7.1/full-rv32imafc-rom4k-l24k/validation.json`；该版本与
实验基点间 `gateware/` 和 CPU 生成器没有已提交代码差异，但本轮没有重新构建 C 对照。
第二轮较该报告减少 369 Logic、2 BSRAM，但 CLS 仍为 98.81%，不能据此推断布线可通过。

两轮完整工具报告在对应 run 的 `gateware/impl/pnr/project.rpt.txt`；机器可读汇总在
`build/experiment-results.json`。前两轮均未完成布线，无法进行 setup/hold 验收；后续参数扫描结果见下节。


## 布局/布线参数扫描

本节记录更改地址之前的 1.5 MiB 候选；当前 1 MiB 候选见下一节。

固定第二轮源码、CPU RTL、功能开关和时序约束，新增运行 `place/route=1/2、3/2、2/1`。
扫描目录为 `build/pnr-sweeps/20261007T111246Z/`，完整参数、输入指纹、各组合结果在 `results.json`。
三组的顶层 RTL 和 SDC SHA256 一致，且源码、CPU RTL 指纹与此前 `2/2` 候选一致。
发现 setup/hold 0/0 后停止启动新组合。`1/2` 完成后仍失败；`2/1` 运行较久且未给出结果，确认合格候选后主动中止，保留日志。

通过候选为 `p3-r2/`，构建 ID：
`da9a3d8-full-rv32imaf-rom0k-l24k-noc-xip-p3-r2-2162a2de-fa49cc83`。

- Gowin 报告与 `validation.json` 均为 setup=0、hold=0；布线完成。
- Logic 19428/20736，Register 11308/16173，CLS 10249/10368，BSRAM 40/46。
- Bitstream SHA256：`2162a2dee4b4818cee54e3e98f714aa732850acf3cbb98434b648c940e80bdb6`，已重算核对。
- CPU SHA256：`7e637a12cbe44097bcb5d9342636ea87bee66de577350d594bbf27ee85fb5b84`。
- `firmware/xip.bin` SHA256：`59a7b55c5ab5fafacf0be6ea4ab2807a369924546c94f7119d9df5ecbc4fe7a4`。
- 新通过候选与之前仿真验证的 RTL/CPU 源输入一致。本轮只改变布局/布线参数，未重跑功能仿真。
- PnR 通过不代表板测通过。未下载 FPGA、未写 Flash、未做实板冷启动或性能测量，未 pin current。

| place / route | 结果 | 未布通网络 | Setup / Hold |
| --- | --- | ---: | --- |
| 2 / 2（上一轮） | 布线失败 | 46 | 无有效结果 |
| 1 / 2 | 布线失败 | 46 | 无有效结果 |
| 3 / 2 | PnR 通过 | 0 | 0 / 0 |
| 2 / 1 | 主动中止 | 未知 | 未知 |

`2/1` 未运行到结束，不计为 PnR 失败。找到通过候选后，其他尚未启动的参数组合没有继续扫描。

## 1 MiB 布局与实板结果

用户指定从 1 MiB 开始用作 ROM 空间后，将 XIP offset、CPU reset vector 和 linker origin 一起修改。
区间 `[1 MiB, 2 MiB)` 用作启动 ROM，BIOS 镜像仍从 2 MiB 开始。本次仅写入 3712 字节启动代码。
构建 ID：`da9a3d8-full-rv32imaf-rom0k-l24k-noc-xip-1m-6ed1a11d-47082f29`。
产物目录：`build/runs/20261007T115649721927Z-da9a3d8-dirty-full-rv32imaf-rom0k-l24k-noc-xip-1m/`。

- full 配置，C 扩展关闭、片内 ROM 0、4 KiB L2；place/route 3/2，setup=0、hold=0。
- Logic 19428、Register 11308、CLS 10249、BSRAM 40。
- FS SHA256：`6ed1a11dadaee93b2253371bc06b78bd1092f80f50082bfa55a71d72ee245f74`。
- XIP SHA256：`687e86ec17f9e72595f6aea1f10edc7725db01af4f3b24856deac4b0dbfe9992`。
- 新地址 Flash 模型与实际 CPU/NOR 联跑通过，CPU 从 `0x20100000` 取指，misa.C=0。
- Flash 写入使用 Gowin operation 32、`--mcuFile xip.bin --spiaddr 0x100000`。`--fsFile` 不适用于此操作的原始程序输入；该尝试报错。随后出现容量误识别与 SPI Verify failed，保留全部日志于 `build/flash-xip-1m-program*.log`。
- 由于下载器 Verify 失败，另行 UART 加载只读 DDR 探针，从 XIP 区读取全部 3712 字节。逐字节比较通过，读回 SHA256 与输入相同，证据为产物目录内 `xip-readback-verification.json`；探针脚本与原始读回在 `build/verify_xip_readback.py`、`build/xip-readback/`。
- SRAM 加载本次 FS 后，UART 显示 XIP banner、DDR READY、FLASH READY 和启动菜单；UART 加载本次 BIOS 后完整 `firmware_verify.py --program --soak-seconds 60 --mic` 通过。`firmware-verification.json` 包含匹配的 FS/app SHA256，UART 原始记录同目录保存。
- FPU/MMU/ISA/L2/fence/UART/IRQ/DDR/Flash、SD/块 DMA、LCD/SPI LCD、板 IO、音频、Ethernet 内部测试、USB/重启/输入、双麦克风、60 秒综合 soak 通过。soak 36 轮，音频 underrun/overrun/error 均为 0，LCD underflow 为 0。
- 未观察屏幕、听音、物理键鼠/LED，未验证外部 Ethernet 数据包。FPGA gateware 仅加载 SRAM，未替换持久配置；未验证断电后的新 gateware 冷启动。BIOS 通过 UART 加载 DDR，未安装新 BIOS 到 Flash。

板上读回探针结束后重新 SRAM 加载候选并 UART 加载 BIOS，使板子恢复 BIOS 运行；未 pin current、未提交。


## 默认 RAM 零基址与动态 VRAM（2026-10-08）

本节是实验分支当前布局，替代上文旧地址。此次迁移不增加布局 flag，不保留旧地址模式。
CPU 生成器默认将 `0xF0000000` 以上视作设备访问，但排除可缓存的 4 MiB XIP 窗口。
旧生成 CPU 的地址属性不匹配，新构建会要求重新生成 CPU RTL。

| 空间 | CPU 物理地址 | 说明 |
| --- | --- | --- |
| DDR RAM | `0x00000000–0x07FFFFFF` | 当前 128 MiB，可向高地址扩展 |
| 启动工作 RAM | `0x007FF000–0x007FFFFF` | 初始化 DDR 前使用的固定 L2 启动页 |
| BIOS | `0x00800000` | 当前链接位置，4 MiB 链接预算 |
| Payload | `0x01000000` | 当前默认装载位置 |
| CSR | `0xF0000000` 起 | LiteX 设备寄存器 |
| Ethernet 缓冲区 | `0xF1000000` 起 | 设备 MMIO |
| USB | `0xF2000000` 起 | 设备 MMIO |
| Flash XIP | `0xF3000000–0xF33FFFFF` | 映射 Flash 物理 `[0,4 MiB)`，只读、可缓存 |
| XIP reset | `0xF3100000` | Flash 物理 1 MiB 启动代码 |

RAM 的软件缓冲区不属于设备 MMIO。VRAM 不再占用硬编码的顶端窗口：
LCD `base0`/`base1` CSR 提供两个 DDR 基址，DMA 在每帧开始时锁存选择与地址。
基址要求 16 字节对齐，完整 480×272×RGB565 帧必须处于 DDR 内，零页不接受。
固件 HAL `hal_video_set_buffers` 还检查两个帧缓冲区不重叠，并要求显示关闭且 DMA 空闲。
BIOS 默认通过链接器分配两块缓冲区，本次实际地址为 `0x0081BC80` 和 `0x0085B880`。
这两个地址是本次 ELF 的分配结果，不是硬件规定的 VRAM 地址。
`0x07E00000` 仍是既有 payload/audio 的软件预算上限，不再表示 VRAM 起点。

`flash_xip_enable` CSR 复位为 1。写 0 后新总线读取返回错误，在途 SPI 读取正常结束；
重新写 1 可恢复读取。关闭不会自动清除 CPU 已缓存的 XIP 内容，软件必须先在 DDR
运行，再完成相应 cache/fence 操作；该 CSR 不是 MMU 权限隔离机制。
启动代码与 BIOS 仍为两个镜像，BIOS 由 UART 加载 DDR，本次未合并 bootloader/BIOS。

合格构建目录：
`build/runs/20261008T014335542590Z-da9a3d8-dirty-full-rv32imaf-rom0k-l24k-zero-ram-dynamic-vram-default/`。

- full、RV32IMAF、ROM 0、L2 writeback 4 KiB、CPU/sys 60 MHz、DDR 120 MHz，place/route 3/2。
- PnR Setup/Hold 0/0；Logic 19562、Register 11398、CLS 10180、BSRAM 40。
- FS SHA256 `1d7c8a1743be11e5d4ed5557d4be39d8895bc42c584e6cd55ac9186c84580b31`。
- XIP 3712 字节，SHA256 `d7bb75cde3fec6a7a81510d473c11ae52734b4d641ba956f5a6e32cc026b735a`。
- BIOS image SHA256 `fc9daef778802929fc302d4938c13b29621fcc55b2eb68aea71a3641b6fe4487`。
- 启动工作 RAM、L2 模型与发射 RTL、CPU fence/AMO/LRSC/MMIO、Flash XIP 波形、CPU+NOR 联跑通过。
- LCD 发射 RTL 测试通过：两个动态基址、帧内 CSR 更新锁存、对齐/零页/DDR 上界拒绝。
- 实板 SRAM 下载后，DDR 启动和 UART BIOS 装载通过；完整 BIOS 验证 43 组命令通过，含双麦克风和 60 秒 soak。
- soak 完成 36 轮；LCD underflows=0，audio underruns/overruns/errors=0，USB errors=0。
- Flash 只写此前授权的物理 1 MiB 启动代码。下载器再次报告 SPI Verify failed；独立 DDR 探针通过 XIP 完整读回 3712 字节，逐字节和 SHA256 相等。关闭/重开 enable CSR 后读取恢复也通过；关闭时总线错误由 RTL 测试验证。
- 证据为候选目录的 `validation.json`、`xip-readback-verification.json`、`firmware-verification.json` 和 UART 原始日志。下载器日志在 `build/flash-zero-ram-program-absolute.log`。

板子最后保留本次 BIOS 运行。未更新持久 gateware、未安装 BIOS 到 Flash、未验证断电冷启动；
当时未进行屏幕/听音/物理键鼠观察或外部 Ethernet 数据包验收，OpenSBI/U-Boot 尚未独立验收；后续结果见下一节。


## 新布局 OpenSBI / U-Boot 验收（2026-10-08）

本节补充上文尚未验证的启动链。沿用相同合格 FS 与 BIOS，不改变 gateware、启动 Flash 内容或设备内存布局。
重新构建 U-Boot v2026.07 与固定 OpenSBI 6ad246a1494807ee91e36ffcf60e4c30bb309d45，
经 BIOS TFTP 装载 OSB1，OpenSBI 位于 0x01000000、DTB 0x01080000、U-Boot 入口 0x01100000。

检查并修复：U-Boot defconfig 的链接/装载/栈地址仍是旧布局；OpenSBI 的 FW_TEXT_START 和探针页表索引也需要迁移。
U-Boot 默认 ISA_C 开启，命令行 PLATFORM_CFLAGS 没有覆盖架构生成的 -march；显式关闭 ISA_C，libgcc 改为 RV32IM/ILP32。
新增 XIP CSR 使后续 CSR bank 偏移，运行时 DTS 的 SD/USB 控制地址改为从当前 csr.json 提取。
SD 驱动缓冲区只有 4 KiB，却声明更大的 b_max；改为 8 个扇区并检查长度边界，补充 DMA cache 同步和失败状态输出。
静态非 PIE 链接还造成搬移后的初始化代码与旧命令表访问不同的 LMB 实例，导致 FAT 装载被拒绝。
构建使用 GCC 编译、LLD PIE 链接并明确 -Ttext=CONFIG_TEXT_BASE，保留动态重定位，解决这一问题。

最终镜像 U-Boot bin 395067 字节，SHA256 3d4628ddb1b0c695187803b3f61a6fc3fb1a2c56d70ada7c82b00076a5ed3a89。
UBOOT.OSB 1443680 字节，SHA256 b3a2a019d17d781030da2c3795757b47279edb549b81cbbe2314240eb9bcf0be。
独立 OpenSBI probe 镜像 1051784 字节，SHA256 760b462a9e385fbf02262a5d6b6d2a8046463152c355a24b3d6f619ec3077882。

最终上板通过：

- U-Boot 控制台、RAM 起点 0 / 大小 128 MiB、LMB memory.count=1、reserved.count=2。
- 最终 ELF 反汇编 72924 条指令，16-bit 指令数量 0。
- 原始 SD 32 扇区读取通过（驱动自动按 8 扇区分段）；此前 1/8/9 扇区边界读取也通过。
- FAT 目录列出 38 个文件、5 个目录；RVTEST00.BIN 装载 4096 字节，CRC32=08040e1e，与 BIOS 结果相同。
- RVT00020.BIN 装载 65536 字节；boot.json 装载 123 字节。
- USB 枚举根 hub 与 Logitech USB Receiver；Ethernet ping 169.254.25.153 成功。
- 独立 OpenSBI probe 的 Base、time CSR、AMO/LRSC、S timer/WFI、S external、U ECALL/time、Sv32 页权限检查通过。
- SBI warm reset 后重新 XIP 启动、DDR 训练、UART 恢复匹配 BIOS 通过。

完整结果位于 build/uboot-zero/verification.json；最终 U-Boot UART 原始记录为
build/uboot-zero/verify-final-pie-uart.log，SBI 原始记录为 build/opensbi-zero/probe-uart.log。
该轮测试结束恢复同一候选 BIOS。没有验证内核启动、SD 写入、USB 存储或物理键盘输入。
Git 中保留 [验收结果](../reports/validation/zero-ram-uboot.json)。原始 UART 记录保留在上述 build 目录，并随提交快照归档。

SD buffer 扩大讨论：当前 bounce buffer 位于 DDR，不消耗 BSRAM；硬件 DMA 单次长度仍限制为 4 KiB。
软件可通过分段完成更大的请求，本次已验证 64 KiB 文件。若要单次 DMA 达到 64 KiB，还需拓宽长度与计数器并重新 PnR/板测。
本轮没有扩大硬件 DMA 上限。
