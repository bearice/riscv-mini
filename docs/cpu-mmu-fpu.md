# MMU / FPU 资源评估

2026-10-02，在独立输出目录评估 60 MHz CPU、120 MHz DDR 的容量和时序。
正常 `scripts/build.py` 仍使用已验收的 VexRiscv lite；以下 CPU 容量实验没有
上板或改写 Flash。后续 USB 替换验收仍使用 lite，并切换到 LCD 输入 demo。

## CPU 配置

本地 LiteX 的 `full` 和 `linux` 预生成核均不含 FPU，因此重新生成 CPU：

- VexRiscv 源码版本 `b6118e5cc2a33323425df6455697139021d50c72`。
- 使用已安装 `pythondata_cpu_vexriscv` 的 Wishbone generator，加一个 FPU
  配置选项；不修改 `.venv` 中的依赖。
- 2 KiB I-cache、2 KiB D-cache，32-byte cache line，各 1 way。
- Linux CSR 配置，M/S/U 特权状态、Sv32 MMU，I/D TLB 各 4 项。
- 迭代整数乘除、迭代移位；不开 debug，不支持压缩指令。
- FPU 配置为 F32 单精度，包含加减、乘法/FMA、除法、平方根及转换；不含 F64。
  `withDivSqrt=false`，但 `withDiv=true/withSqrt=true`，使用两个现有的独立
  运算实现。旧的共享 `withDivSqrt=true` 分支会触发上游
  `Need to implement commit tracking` 断言，不能用于本轮评估。

因此与 lite 的差值包含 D-cache、MMU/特权控制和 FPU，不能全部归算为 FPU。
FPU 需要 D-cache 的说明见 [VexRiscv 官方文档](https://github.com/SpinalHDL/VexRiscv)。

CPU RTL SHA256：

| 配置 | SHA256 |
| --- | --- |
| MMU | `3c8f4afa1841fd5afedfb6cc58bcc3ac39b132a64bb8f9a84d1ef2e3345844c3` |
| MMU + FPU | `45333b754ab68726f6eb23ede586af40cb5f2d7990569a60fcc8b6f9cd45fc97` |

两次独立生成逐字节相同，记录见
`build/cpu-qualification/reproduce-generator/generator.json`。CPU 生成过程的
成功日志为 `cpu-generate-fpu.log` 和 `reproduce-generator/generate.log`；
首次共享除法/平方根路径失败的日志为 `cpu-generate.log`，均位于
`build/cpu-qualification/`。

## 资源结果

容量：Logic 20,736、Register 16,173、CLS 10,368、BSRAM 46、PLL 4。
以下成功配置的数据来自布局布线后报告；超容量配置只有综合结果。

| 配置 | Logic | Register | CLS | BSRAM | PLL | Setup / hold 违例 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 已验收 lite，全外围 | 16,275 | 8,714 | 9,131 | 36 | 4 | 0 / 0 |
| MMU，全外围 | 18,396 | 9,904 | 9,903 | 41 | 4 | 0 / 0 |
| MMU + FPU，全外围 | 21,306（综合） | — | — | — | — | 超容量，未进行 PnR |
| MMU + FPU，关闭 USB | 18,235 | 10,206 | 9,878 | 43 | 2 | 0 / 0 |
| MMU + FPU，关闭 Ethernet | 20,442 | 11,085 | 10,361 | 30 | 4 | 布线失败，不能验证时序 |
| MMU + FPU，关闭 Ethernet 和音频输出 | 19,414 | 10,622 | 10,227 | 29 | 4 | 0 / 0 |

全外围 MMU + FPU 在综合阶段报 `RP0006`，Logic 超出 570 个（约 2.75%），
不能生成合格配置。关闭 USB 后可以通过，但剩余 CLS 为 490（约 4.7%），
BSRAM 为 3；DSP 为 3.75 / 24。Gowin 的 DSP 使用量以折算的硬件块计，
包括 1 个 MULT9X9、3 个 MULT18X18、2 个 ALU54D，不应把这些子项直接相加。

仅关闭 Ethernet 的配置综合使用 20,196 Logic，通过容量检查；PnR 使用
20,442 Logic、10,361 CLS（99.93%，只剩 7 个），最终报 `PR0004`，有
122 条未布通网络，没有合格 bitstream 或最终时序报告。PnR 用时
8 分 31 秒。表中该行是失败运行的资源统计，不能当作可用配置。
这次试验使用统一的既有 PnR 参数，未穷举放置/布线策略，不能据此断言所有
其他策略都会失败；但当前参数下仅删除 Ethernet 不足以得到合格系统。

继续关闭音频输出后，完整 PnR 通过，setup/hold 均为 0。CPU/DDR 仍为
60/120 MHz，USB、RGB LCD、SPI LCD、原生 SD、Flash 和两路麦克风输入均保留。
Logic 剩余 1,322，CLS 剩余 141（占用 98.64%），BSRAM 剩余 17；DSP 为
3.75 / 24，PLL 仍为 4 / 4。PnR 用时 5 分 46 秒。该配置通过本轮约束下的
资源和时序检查，但没有上板或软件适配；它只证明这组外围裁剪可实现。
bitstream SHA256 为
`1250f66f83d9d23eae7a1da0ec11cdef327dae913e27e14275d92d6ea876b143`。

基线证据为 `build/modular-full-good/validation.json`；本轮证据为
`build/cpu-qualification/<case>/qualification.json` 与
`gateware/impl/pnr/project.rpt.txt` / `project_tr_content.html`。
对应 case 为 `mmu-full`、`mmu-fpu-full`、`mmu-fpu-no-usb`、`mmu-fpu-no-eth`。
新增 case 为 `mmu-fpu-no-eth-audio`。
全外围超容量的原始错误在 `mmu-fpu-full/gateware/impl/gwsynthesis/project.log`。
布线失败的原始错误在 `mmu-fpu-no-eth/gateware/impl/pnr/project.log`。

## 复现

Java 8 和 sbt-launch 1.9.7 放在本轮 `build/cpu-qualification/` 下，未安装到
全局环境。准备上述版本的 VexRiscv 源码后，可使用：

```powershell
.venv/Scripts/python.exe scripts/cpu_generate.py --vexriscv-source build/cpu-qualification/generator/ext/VexRiscv --java build/cpu-qualification/tools/jdk8u504-b01/bin/java.exe --sbt-launch build/cpu-qualification/sbt-launch.jar --output-dir build/cpu-qualification/reproduce-generator

.venv/Scripts/python.exe scripts/cpu_qualify.py --usb-backend ohci --cpu-verilog build/cpu-qualification/reproduce-generator/VexRiscv_Mmu.v --output-dir build/cpu-qualification/mmu-full
.venv/Scripts/python.exe scripts/cpu_qualify.py --usb-backend ohci --cpu-verilog build/cpu-qualification/reproduce-generator/VexRiscv_MmuFpu.v --output-dir build/cpu-qualification/mmu-fpu-full
.venv/Scripts/python.exe scripts/cpu_qualify.py --usb-backend ohci --cpu-verilog build/cpu-qualification/reproduce-generator/VexRiscv_MmuFpu.v --without eth --output-dir build/cpu-qualification/mmu-fpu-no-eth
.venv/Scripts/python.exe scripts/cpu_qualify.py --usb-backend ohci --cpu-verilog build/cpu-qualification/reproduce-generator/VexRiscv_MmuFpu.v --without usb --output-dir build/cpu-qualification/mmu-fpu-no-usb
.venv/Scripts/python.exe scripts/cpu_qualify.py --usb-backend ohci --cpu-verilog build/cpu-qualification/reproduce-generator/VexRiscv_MmuFpu.v --without eth --without audio --output-dir build/cpu-qualification/mmu-fpu-no-eth-audio
```

所有外围、DDR 和时钟使用当前模块化配置；保留真实模块层级及既有 SDC。
Gowin V1.9.12.04，器件 `GW2A-LV18PG256C8/I7`，PnR 参数仍为
`timing_driven=1, place_option=2, route_option=1`。

## CSR 资源归属

Gowin 的 `gateware/impl/gwsynthesis/project_syn_resource.html` 列出模块自身的
资源；本轮逐行相加与综合报告的整机 LUT、ALU、Register 和 BSRAM 数量一致。
父模块行不包含子模块的累计值，计算某一子树时应把该子树各行各加一次。
`csr_bankarray` 本身为零，实际资源在它的 `csrbank_*`、`csrstorage_*` 等子模块。

LiteX 把各外围的 CSRStorage、CSRStatus 和 CSRBank 统一收集到这里。
因此外围模块行不包含它全部的寄存器接口成本；此处 CSR 是外围控制/状态接口，
与 VexRiscv 内部的 `mstatus`、`satp` 等 CPU CSR 不同。

| 综合配置 | CSR 子树 LUT | CSR 子树 Register | 整机 Logic | CSR LUT / 整机 Logic |
| --- | ---: | ---: | ---: | ---: |
| MMU + FPU，关闭 Ethernet | 1,544 | 1,399 | 20,196 | 7.65% |
| MMU + FPU，关闭 Ethernet 和音频输出 | 1,318 | 1,284 | 19,184 | 6.87% |

关闭音频输出后，整机综合 Logic 减少 1,012；其中 CSR 子树减少 226 LUT、
115 Register。这是两次整机综合的差值，包含互连优化，不能当成独立音频模块
的精确面积。两路麦克风输入仍保留。

后一配置中，`csrbank_10` 是 SD 控制器和双向 DMA 的寄存器接口，单独占
437 LUT；`csrbank_11` 是 SDRAM 控制/状态接口，占 217 LUT；麦克风接口
`csrbank_6` 占 154 LUT。配置寄存器使用触发器保存，地址译码、写使能和
32-bit 读回选择使用 LUT；多数 bank 还使用 32 个触发器寄存读回数据。
只读状态 CSR 通常直接连接原模块已有状态，不会为每一位再造一份存储。
地址空间每个 bank 保留 0x800 bytes，并不表示实现了 0x800 bytes 的 RAM。

证据在以上两个 case 的 `module-resources.json`、原始层级资源报告及
`gateware/modules/riscv_mini__csr_bankarray__csrbank_*.v`；实现来源为本地
`litex/soc/interconnect/csr_bus.py:CSRBank`。接口说明见
[LiteX CSR Bus](https://github.com/enjoy-digital/litex/wiki/CSR-Bus)。

## 轻量 USB 后端复测

`--usb-backend ultra` 是后续新增的默认后端；以上历史结果及复现命令使用
OHCI。保留全部外围的 MMU + FPU 复测结果如下：

| PIO USB 实验 | Logic | Register | CLS | BSRAM | PLL | 结果 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 初期通用总线桥原型 | 19,366 | 10,750 | 10,165 | 43 | 3 | setup/hold 0/0，但桥后来在 lite 实板失败 |
| 第一版寄存桥 | 19,627 | 10,823 | 10,233 | 43 | 3 | PR0004，101 根网络未布通 |
| 最终同步复位寄存桥 | 19,451 | 10,823 | 10,187 | 43 | 3 | 布线完成，setup 613 / hold 0，时序失败 |

分别见 `build/usb-light/mmu-fpu-full/qualification.json`、
`mmu-fpu-registered/qualification.json` 和 `mmu-fpu-final/qualification.json`。
最终报告的最差 setup slack 为 -4.548 ns，涉及 I-cache/MMU/fetch PC 到
cache bank 的 CPU 路径；另一条 -4.505 ns 路径涉及 FPU 响应及流水线停顿。
原始路径在 `mmu-fpu-final/gateware/impl/pnr/project.timing_paths`。

因此轻量 USB 确实把全外围配置降到容量范围内，但最终实现尚未在 60 MHz
满足时序，不能用早期原型的成功结果替代当前实现的验收。

## Retiming 与 CPU 取指流水实验

2026-10-02 在独立构建目录尝试 Gowin Retiming、增加 VexRiscv 取指流水级，
以及 Routability 优先的布局。配置均保留全外围、CPU 60 MHz / DDR 120 MHz、
轻量 USB 后端；没有改动已验收 bitstream，也没有下载实验配置。

基线 PIO USB MMU+FPU 核 RTL SHA256 为
`45333b754ab68726f6eb23ede586af40cb5f2d7990569a60fcc8b6f9cd45fc97`。
基线完整 PnR 使用 `retiming_resource=none, place_option=2, route_option=1`，
可布通但 setup 违例 613、hold 违例 0，最差 setup slack `-4.548 ns`；
另一条 FPU 响应路径为 `-4.505 ns`。路径见
`build/usb-light/mmu-fpu-final/gateware/impl/pnr/project.timing_paths`。

| 实验 | PnR 选项 / CPU 变化 | Logic | CLS | BSRAM | 最终结果 |
| --- | --- | ---: | ---: | ---: | --- |
| 基线 | `none / place 2 / route 1` | 19,451 | 10,187 | 43 | 可布通；setup 613、hold 0 |
| 额外取指级 | `all / place 2 / route 1`；`relaxedPcCalculation=true` | 19,749 | 10,255 | 43 | PR0004，435 根未布通 |
| 额外取指级重放 | `all / place 1 / route 1` | 19,749 | 10,255 | 43 | PR0004，仍为 435 根未布通 |
| 全资源 Retiming | `all / place 2 / route 1` | 19,451 | 10,177 | 43 | PR0004，773 根未布通 |
| DSP 定向 Retiming | `dsp / place 2 / route 1` | 19,451 | 10,187 | 43 | PR0004，67 根未布通 |
| DSP 定向 + Routability 放置 | `dsp / place 1 / route 1` | 19,451 | 10,187 | 43 | PR0004，仍为 67 根未布通 |
| BSRAM 定向 Retiming | `bsram / place 2 / route 1` | 19,451 | 10,169 | 43 | PR0004，438 根未布通 |

`relaxedPcCalculation` 确实生成了多一级取指前端寄存级，CPU RTL SHA256 为
`66a3daa96db85932e8441716382c0829b1747d4d2872d6e09c9298a17cb524f1`；它不是
增加执行级或重排 FPU 运算级。该变体综合通过容量检查（Logic 19,542、
Register 10,825、BSRAM 43），但 PnR 后 CLS 达到 10,255/10,368；两种放置
算法均有 435 根未布通网络。不能从这两次未完成路由的结果推断它的时序。

PnR 的 `-retiming_resource all/dsp/bsram` 确实出现在各自的 `cmd.do` 中；
但所有这些配置都因 PR0004 停在路由阶段，没有有效 timing report，不能
判定 setup 是否改善。全资源、DSP、BSRAM 目标分别留下 773、67、438 根
未布通网络；DSP 案例改用 `place_option=1` 后仍是 67 根。

另外，`--retiming` 和 `--pipelining` 会在 Gowin Tcl 请求
`set_option -retiming 1` / `set_option -pipe 1`。在本机 GowinSynthesis
生成的 `project.prj` 有效 `OptionList` 中，这两个选项没有被记录；当前保留
的综合选项包括 `opt_goal=auto`、`map_option=1`、`max_fanout=1000`。
所以本轮确实向 PnR 下达了资源定向 Retiming 选项，但不能声称 Gowin 综合器
执行了通用 Register Retiming 或乘法器 `-pipe`。`place_option=1` 是 Gowin
标注的 routability-priority 算法；试验没有消除上述拥塞。

实验逐项证据保存在：

- `build/usb-light/retiming-pipeline-syn/qualification.json`、
  `pnr-retiming-pipeline.log` 和 `pnr-retiming-pipeline-place1.log`；
- `build/usb-light/retiming-only/qualification.json`；
- `build/usb-light/retiming-dsp/qualification.json`；
- `build/usb-light/retiming-dsp-place1/qualification.json`；
- `build/usb-light/retiming-bsram/qualification.json`。

每个完整 PnR 的 `gateware/impl/pnr/project.rpt.txt`、`project.log`、
`cmd.do` 记录未布通数、资源和实际工具参数。综合重定时请求与实际 PnR
Retiming 的区别也反映在 `scripts/cpu_qualify.py` 的参数说明中。

结论：这轮测试中，全功能 MMU+FPU 系统均未同时达到“PnR 完成、60 MHz
时序通过”。唯一完成路由的 MMU+FPU 全外围基线仍有 613 条 setup 违例；
所有测试的 retimed 或额外取指级变体都未布通，因此全功能配置目前不能
作为可用实现。这个结论限定于上述 Gowin 参数和测试变体，并不证明所有
可能的 RTL 重构或频率组合都不可能实现。

## 软件与上板验证边界

`qualification.json` 是容量/时序实验报告。ROM 内容复用已验收的
`build/modular-full-good/firmware/boot.bin`，以保留真实 ROM 资源占用；没有为
新的 CPU 编译应用或生成可安装镜像。它不是 `validation.json`，不能作为
下载依据。

本轮没有执行浮点指令、页表转换或外围 DMA 的实板功能测试。正式启用还需
处理 D-cache 与显示/SD/USB/音频 DMA 的一致性、装载后的代码同步、FPU 状态
初始化/上下文保存，以及需要分页时的特权级、页表和异常处理。资源和时序
通过不代表这些软件适配已经完成。


## SPI SD full-peripheral qualification (2026-10-02)

CSR-packed full MMU+FPU with SPI SD passed PnR at CPU/sys60 MHz and DDR120 MHz:
Logic16572, Register9745, CLS9371, BSRAM41, PLL3; setup/hold0/0, reported
worst setup slack +0.944 ns. This supersedes earlier no-qualified-full-system
conclusions for the SPI variant. Native SD remains unqualified; there was no
post-CSR-packing native PnR comparison. No software adaptation or board
validation was performed; defaults remain lite/native. See
[SPI qualification](spi-mmu-fpu.md) for evidence and reproduction.
