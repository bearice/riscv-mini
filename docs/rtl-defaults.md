# 分模块 RTL 默认值与复位优先级诊断

2026-10-03：默认 full profile 改为 MMU+FPU、SD lite、DDS 和全部外设。minimal 默认关闭 MMU/FPU；独立关闭开关保留。

## 可复现的语义差异

Migen 未赋值的内部 `Signal` 仍有 `reset` 值。flat 转换器把它声明为带初值的 `reg`；如果没有其他赋值，它会保持该值。分模块转换器会把跨模块信号声明为互连 `wire`，并通过 input 端口传给子模块，却没有为这种未赋值信号生成驱动。

例如 LiteX Wishbone SRAM 没有主动驱动 `bus.err`，原 flat 输出保持 0。原分模块整机的 `minisoc_interface_err0/1` 却是未驱动的 wire。实际整机模块仿真得到 `rom=z sram=z`。这是生成器产生的语义差异，不是 Verilog 模块化本身改变了逻辑。

Yosys 对整机生成 RTL 做 `proc; flatten; check`，原分模块版本有 188 个非 CPU 未驱动位；flat 为 0。它们不仅包括 ROM/SRAM ERR，还有只读 DMA 的未使用写总线字段、部分 stream 标志和 CSR 默认值。外部 CPU RTL 内另有 36 个检查警告，在 flat、原分模块和修复版中完全相同；本次没有修改 CPU RTL，也不把这些警告当作新增的模块边界故障。

## 修复

`gateware/rtl.py` 在实际 FHDL 中计算未驱动内部信号，排除顶层 IO、时钟/复位、赋值目标和 special 输出。对于层级转换后变成互连 wire 的信号，在声明它的连接模块内生成 `assign signal = reset_value`；已有初始化的本地 reg 保持原样。尊重 `regs_init=False`，该模式不补初值。

这样同时保留默认 0 和默认 1，避免在每个外设中分别打补丁。整机补回 98 条默认赋值；仍有 262 个 Verilog 模块。修复后整机非 CPU 未驱动位为 0，ROM/SRAM ERR 实际模块仿真通过。

## 回归与现场记录

`tests/rtl_defaults_test.py` 使用真实的 Wishbone SRAM，另加入默认为 1 的跨模块信号。它调用实际的层级转换适配器，可用 `--unfixed` 复现旧路径：

| 版本 | ERR | 默认为 1 的信号 | 结果 |
| --- | --- | --- | --- |
| 未修复分模块 | z | z | FAIL |
| 修复后分模块 | 0 | 1 | PASS |
| flat 对照 | 0 | 1 | PASS |

生成并执行测试（Linux 工具在 PATH 时）：

```sh
python tests/rtl_defaults_test.py --output-dir build/rtl-defaults-test --run
python tests/rtl_defaults_test.py --output-dir build/rtl-defaults-before --unfixed --run
```

Windows 可加 `--wsl-distro NixOS --iverilog /nix/store/.../bin/iverilog --vvp /nix/store/.../bin/vvp`。本轮准确工具路径和执行输出保存在 `build/rtl-diagnosis/regression-*.log`。

旧的 `build/native-optimized/board/gateware/riscv_mini.fs` 再次 SRAM 下载成功（状态 `0x00006020`），8 秒 UART 窗口内没有启动信息，`BOOT_BANNER_FAIL`。随后重新下载已验收的 DDS flat 镜像并通过 UART 装载应用，正常回到 monitor；没有更新 Flash。

证据目录 `build/rtl-diagnosis/`：

- `before-board.log`：实际旧镜像无启动复现。
- `restore-flat.log`：已验收版本恢复。
- `hier/error.log` / `fixed/error.log`：实际整机 ROM/SRAM ERR 失败/通过。
- `flat/check.log` / `hier/check.log` / `fixed/check.log`：整机驱动检查。
- `regression-before/result.log` / `regression-after/result.log` / `regression-flat/result.log`：最小回归。
- `rtl-defaults-evidence.json`：补回的默认值、所属模块及所有日志入口。

## 路由与验证边界

修复版默认保留综合层级的首次整机路由失败，有 1,245 条未布通网络。报告为 Logic 19,051、Register 10,776、CLS 10,114、BSRAM 43、PLL 3；没有合格 bitstream，未下载。

因此继续试验保持分模块 Verilog 源文件，但让 Gowin 综合展开网表（`netlist_hierarchy=0`），允许跨模块优化。实验目录为 `build/rtl-diagnosis/flatten-trial/`。不能把 RTL 检查通过当作整机时序或上板验收通过；最新 PnR/上板结果应查看单独报告。

第二次整机 PnR 使用 `netlist_hierarchy=0`、`timing_driven=1`、`place_option=2`、`route_option=1`，路由完成，Setup/Hold 均为 0。该选项已固化在 `split_verilog` 适配器中；Verilog 源模块仍保持分开。资源为 Logic 19,055/20,736、Register 10,776/16,173、CLS 10,143/10,368、BSRAM 43/46、PLL 3/4、PRIMARY 和 LW 均为 8/8。

`qualified-full/` 是重新生成的默认 full 构建。复用试验的 PnR 产物前，`qualify_trial.py` 核对了全部 262 个模块、CST、SDC、ROM 初始化文件（共 265 项）、CSR JSON、boot.bin 和 app.img 的逐字节一致性，以及四个综合选项。`source-identity.log` 和 `qualified-full/validation.json` 保存证明及哈希，不把重新生成的 host 构建伪称为再次运行 PnR。

Bitstream SHA256 为 `4b1f4bee552027f40e2bdd400430c66b68de02f25fba46b133c9a0c430b52fa9`。CPU/sys 保持 60 MHz、DDR 120 MHz、音频及麦克风为 DDS 48 kHz；没有因修复改变频率。

进一步执行整机 `proc; flatten; opt_clean; check`，结果为 0 problems，见 `full-clean-check.log`。此前 36 个 CPU 警告所在的未使用逻辑在清理后消失；不是用默认值补丁掩盖它们。单独 CPU 的未展开检查另有子模块接口警告，不等同于整机驱动故障。

Gowin 仍报告 CLKDIV 自动时钟提示和 USB `usb_ulpi_clk_d` 普通路由资源警告；完整静态时序通过，不代表所有未来布线配置都具备余量。CLS 剩余 225，时钟资源 PRIMARY/LW 已满。

完整 SoC 的首次 UART 仿真还含启动延迟、物理原语模型和事件调度开销；本轮未从该模型取得可靠的完整启动结论。默认值语义由小复现和实际整机信号检查证明；CPU 实际启动需要上板确认。没有据此声称 Gowin 一定将哪条未驱动线优化成了 1。

## 最终网络测试发现的第二处差异

首次仅修复默认值的层级镜像已启动，通过 37 种固件命令和 `test soak 300`
（54 轮，最后一轮完成后约 305 秒）、Line In 音频测量及用户两屏确认。
但实际网络测试只有 ARP 返回正确 MAC，ping 和 UDP 超时。失败报告为
`external-first-failure.json`、`network-probe.json`。随后重新下载原 DDS flat
镜像，同一应用和 USB 网卡通过 ping 及 0..1472 字节的全部 UDP 长度；
见 `flat-external.log` 和 flat 构建中的 `external-verification.json`。
因此首次层级镜像不能视为完整 Ethernet 验收通过。

层级转换器收集 inline 子模块时，先放父模块语句，再放子模块语句。
`ResetInserter` 原本追加在更新之后的复位条件因此移到更新之前，违反
Verilog 同一过程内最后一次非阻塞赋值优先的语义。实际 Ethernet SYS
过程可见 `if(reset_sys)` 位于包缓存更新之前，而 flat 中位于更新之后。

`tests/rtl_defaults_test.py --unfixed-order` 保留默认值补丁，单独复现优先级
故障：父模块对每拍递增的子模块加局部复位，复位保持后计数器为 6，
`RESET_PRIORITY_FAIL`。修复后保持为 0，释放后恢复到 2，
`RESET_PRIORITY_PASS`；flat 是参考路径。

修复在适配器记录最终合并 FHDL 的语句顺序；层级节点 lowering 前按该
顺序重新排列同步语句，保持父子内联后的赋值优先级。只在本次转换期间
包装 LiteX `_prepare_fragment`，`finally` 恢复，不修改安装包。第二次
完整构建目录为 `reset-fixed-full/`；其最终 PnR 和上板结果另行记录。

第二次完整 PnR 于 2026-10-03 01:22 完成，Setup/Hold 均为 0。
修复优先级后 Logic 为 18,560/20,736、Register 为 10,773/16,173、
CLS 为 9,967/10,368、BSRAM 为 43/46、PLL 为 3/4，PRIMARY/LW 均为 8/8。
仍生成 262 个模块；FS SHA256 为
`409b49a98f897ac2372b1f583fe643a6f86e286d0101b9a7fb47916a28e058e4`。
这是独立完整构建，没有复用前一次 PnR。

等待路由期间另生成按 SoC 大模块展开内部的 35 模块对照，CSR 相同，
Yosys 驱动检查为 0 problems；目录 `coarse-full/`。它没有综合或下载，
此时完整层级已成功路由，尚未替换默认输出层级。后续第三次完整层级
路由失败后，默认输出改为下述 SoC 块层级。

## 共享模块别名导致逻辑被删除

第二次镜像的内部检查和 Line In 采集通过，实际 ping 仍失败。
`tx-network-diagnostic.log` 记录 TX 完成 pending=1、enable=1、tx_busy=1，
CRC/preamble/MDIO 错误均为 0。接收包已能复制，发送完成中断却没有到达 CPU。

实际分模块 top 中 `minisoc_wishbone_interface_ev_irq` 为带初值 0 的 reg，
没有逻辑赋值；flat 中明确为 writer_irq OR reader_irq。初始化常量使
Yosys 驱动检查仍然通过，因此 0 problems 不代表功能语义等价。

LiteEth `SharedIRQ` 同一实例在 SRAM.ev 和 interface.ev 两处引用。层级
转换器把第二处表示为别名节点，其 body 为空，但仍携带真实所有者的
statement IDs；后续过滤别名时删除了已经 inline 到父模块的实际 IRQ
OR 语句。适配器现在从生成的所有权树中去掉别名节点，只序列化每个
真实模块一次；不修改 Migen 实例、功能逻辑或依赖安装包。

永久回归中的 CounterParent 也给同一 child 加了 alias。
`--unfixed-alias` 在默认值和顺序修复仍启用的条件下复现计数器一直为 0，
`COUNTER_NOT_RUNNING`；完整修复后默认值、递增、复位和释放均通过。
日志为 `alias-before.log` / `alias-after.log` 及对应目录的 result.log。

第三次完整构建目录为 `alias-fixed-full/`。其 RTL 已恢复实际 OR 语句，
软件 `test eth` 补充只读 CRC/preamble/MDIO、RX pending/slot/length、
TX FIFO/事件/tx_busy 状态，便于后续直接从 monitor 诊断。
第三次构建于 2026-10-03 01:54 结束，124 条网络未布通，没有可验收镜像。
Logic 18,474/20,736、Register 10,774/16,173、CLS 10,050/10,368，
BSRAM 43/46。证据为 `alias-fixed-full/gateware/impl/pnr/project.rpt.txt`。
第二次镜像尚不具备完整网络验收资格。

## 默认模块粒度

默认保留 SoC 直接子模块，将其内部 FSM、FIFO、CSR 实现内联，full 为 35
个生成模块。独立外设模块及端口继续存在；CPU、USB 等外部核保持原有
源文件。这样满足模块化输出，同时减少过深逻辑层级的布线压力。
`--deep-verilog` 保留完整内部层级作为诊断选项；`--flat-verilog` 保留对照。
永久回归仍使用 deep 路径，以确保三处转换修复没有被粒度变化掩盖。

最终候选目录为 `blocks-full/`，重新执行完整编译和 Gowin 流程，未复用
之前的布线或 bitstream。最终资源、时序和硬件验收在完成后记录。

`blocks-full/` 的 place=2 / route=1 在 02:14 结束，仍有 96 条未布通网络。
随后独立构建 `blocks-route2/`，place=3 / route=2，于 02:19 完成，Setup/Hold
均为 0。最终 PnR Logic 18,740、Register 10,775、CLS 10,057、BSRAM 43、
PLL 3；保留 35 个独立 SoC 模块。默认参数现为这组已验收参数。

上板 36 种固件命令通过，SD 新建并读回 `RVT00008.BIN`。
共享 IRQ 修复后的实际 ping/UDP 已通过：301.969 秒、52 轮、520 个 UDP 包，
0..1472 字节、179,556 字节 payload，零超时、无数据不匹配。并发结束时
SD/USB/audio 错误与 LCD 欠载均为 0。左右声道实际 Line In 测量通过，用户
确认两屏正常稳定。证据在该目录的 `firmware-verification.json`、
`external-verification.json` 与对应 UART 日志。

默认命令再次生成 `default-repro/`，37 个 RTL/CST/SDC 文件与 CSR 均和
实际下载候选完全一致；生成 Tcl 的 place/route 也是 3/2。没有复用其 PnR，
该目录仅作为默认值重现检查，见 `default-repro-identity.log`。

Gowin 并行实例没有被证实会互相干扰。之前最小构建在另一编译运行期间
失败，没有足够证据归因于实例冲突；后续 RV32GCB 实验的多个独立目录
综合实例已同时运行并各自产生报告。工具启动失败不能写成许可证或并发
限制的确诊结论。
