# 性能测试报告

生成时间：2026-10-13T05:52:02+00:00。数值来自已保存测量，不表示本命令重新跑板。

## 明确选择的版本

| 项目 | 基线 | 候选 |
| --- | --- | --- |
| id | a50a747-full-rv32ima-rom8k-l28k-doom-palette-l28k-range-p2-702f30b6-9031f6b7 | a50a747-full-rv32ima-rom8k-l28k-doom-default-commit-5d05d711-6c87e75f |
| path | C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini\build\runs\20261013T042949595152Z-a50a747-dirty-full-rv32ima-rom8k-l28k-doom-palette-l28k-range-p2 | C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini\build\runs\20261013T052008451549Z-a50a747-dirty-full-rv32ima-rom8k-l28k-doom-default-commit |
| abi | b881c174 | b881c174 |
| bitstream_sha256 | 702f30b607294712780051270470f927bf787cdfb0b8e9851ef6a1673b9a63cb | 5d05d71161bfc2a0f11442f904dc3fcb9f26d73d6c592f7d326d50ee37e6b373 |
| image_sha256 | 3b010a9302fc01edc749c4f11f2cbfaa37198c83003b1b4798057345dc46b679 | c9fd5e16b78d54dede59752bb5f1751b072ad4f4f4cf57ea095c1923f64af0bd |

## 配置差异

以下差异同时存在，变化百分比不能单独归因于 C 或 fence。


## 测量中位数

| 项目 | 基线 | 候选 | 数值变化 | 样本数（旧/新） |
| --- | ---: | ---: | ---: | ---: |
| cpu.mul-add | 8.177 cycles/iteration | 8.192 cycles/iteration | +0.2% | 3/3 |
| cpu.xorshift | 15.320 cycles/iteration | 15.319 cycles/iteration | -0.0% | 3/3 |
| io.flash-read (65536 B) | 0.580 MiB/s | 0.580 MiB/s | +0.0% | 3/3 |
| io.rgb-fill16 (261120 B) | 6.422 MiB/s | 6.423 MiB/s | +0.0% | 3/3 |
| io.sd-read (512 B) | 0.526 MiB/s | 0.529 MiB/s | +0.5% | 3/3 |
| io.sd-read (4096 B) | 1.809 MiB/s | 1.806 MiB/s | -0.2% | 3/3 |
| io.uart-tx (4096 B) | 0.011 MiB/s | 0.011 MiB/s | -0.0% | 3/3 |
| mem.chase32 (1048576 B) | 2115.157 ns/load | 2113.299 ns/load | -0.1% | 3/3 |
| mem.copy32 (1024 B) | 25.509 MiB/s | 25.507 MiB/s | -0.0% | 3/3 |
| mem.copy32 (4096 B) | 5.446 MiB/s | 5.432 MiB/s | -0.3% | 3/3 |
| mem.copy32 (65536 B) | 5.453 MiB/s | 5.442 MiB/s | -0.2% | 3/3 |
| mem.copy32 (1048576 B) | 5.455 MiB/s | 5.444 MiB/s | -0.2% | 3/3 |
| mem.libc-copy (1048576 B) | 5.361 MiB/s | 5.350 MiB/s | -0.2% | 3/3 |
| mem.libc-set (1048576 B) | 8.638 MiB/s | 8.629 MiB/s | -0.1% | 3/3 |
| mem.read32 (1024 B) | 22.133 MiB/s | 22.136 MiB/s | +0.0% | 3/3 |
| mem.read32 (4096 B) | 13.148 MiB/s | 13.142 MiB/s | -0.0% | 3/3 |
| mem.read32 (65536 B) | 9.081 MiB/s | 9.083 MiB/s | +0.0% | 3/3 |
| mem.read32 (1048576 B) | 9.088 MiB/s | 9.085 MiB/s | -0.0% | 3/3 |
| mem.write32 (1024 B) | 23.831 MiB/s | 23.815 MiB/s | -0.1% | 3/3 |
| mem.write32 (4096 B) | 23.464 MiB/s | 23.471 MiB/s | +0.0% | 3/3 |
| mem.write32 (65536 B) | 8.745 MiB/s | 8.745 MiB/s | +0.0% | 3/3 |
| mem.write32 (1048576 B) | 8.755 MiB/s | 8.749 MiB/s | -0.1% | 3/3 |

周期/延迟越低越快，MiB/s 越高越快。CPU 指标包含循环控制，不是 MIPS。缓存写入计时不能解释为物理 DDR 写回峰值。JSON 保留每组范围和样本数。

## 并发状态和边界

- LCD underflow baseline：`{'palette-l28k-all': [0, 0]}`（每个 UART 中的前后计数）。
- LCD underflow candidate：`{'all': [0, 0]}`（每个 UART 中的前后计数）。
- Same qualified 8 KiB L2/CPU/display configuration rebuilt from final staged source; this comparison checks regressions and does not measure the earlier CPU or palette gains.
- BIOS cache suite supports only 4 KiB and is unsupported on this 8 KiB configuration; not counted as passed. Matched Doom RGB565/INDEX8 gains and visual checks are recorded separately in doom-palette-experiment.md.
- 未匹配候选项目：`[]`；未匹配基线项目：`[]`。

## 原始数据

- baseline：`C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini\build\doom-perf\palette-l28k-all\results.json`，SHA256 `49dd6829890945e84f237c6328284ceb1b79d1bf2ccfe518b49d3f1f7f213044`，身份关联：recorded。
- candidate：`C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini\build\doom-default-perf\all\results.json`，SHA256 `fc1c62328758ac94dfe1d6d0907e6d07678afe4e11dd0654fc78828255dd554a`，身份关联：recorded。

## 默认配置提交验收

- 当前源码的完整 PnR setup/hold=0/0，匹配 FS/BIOS 的固件套件和60秒 soak通过；证据 build/operations/20261013T054845755933Z-verify/firmware-verification.json。
- 三轮 all 共66行均通过，原始测量 build/doom-default-perf/all。
- 默认 INDEX8/软浮点/Newlib 载荷与之前普通上板载荷逐字节一致，SHA256 f1b40e2aa573cedc8e81e0021c5b99e351f7fc3abde11a25d0fc5e243727d07a；显式 --rgb565 编译通过。
- 新 FS 上独立 Doom 验证载荷的 source/video CRC=37fa8643，palette=d89868c8，mapped565=f90257a0，与此前同工作量结果一致。验证载荷的时间不混入无校验载荷的性能统计。日志 build/doom-default-doom-validation-retry.log；第一次探测使用了错误的等待标记，实际 CRC 已输出，原始日志保留在 build/doom-default-doom-validation.log。
- 11项 Doom 主机回归、Newlib合约、mini操作33项、workflow18项、控制台输出，以及调色板/地址边界/8KiB L2/4、8、16KiB启动 RAM 回归通过。同暂停点64000字节INDEX8比较为0差异；人工破坏一个字节后比较工具返回失败。
- BIOS仍freestanding，应用静态链接Newlib。默认硬件输入由 current 指针及其 recipe保存，Doom CLI默认INDEX8和RV32IM/ILP32。提交后从干净HEAD重建快照，并以最终快照验收结果更新current；不写Flash。
