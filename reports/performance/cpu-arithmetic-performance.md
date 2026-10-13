# 性能测试报告

生成时间：2026-10-12T16:59:06+00:00。数值来自已保存测量，不表示本命令重新跑板。

## 明确选择的版本

| 项目 | 基线 | 候选 |
| --- | --- | --- |
| id | a50a747-full-rv32ima-rom8k-l216k-doom-l2-16k-p2-c3eb0652-60616715 | a50a747-full-rv32ima-rom8k-l216k-doom-fast-arithmetic-p3-364728a6-95dd1507 |
| path | C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini\build\runs\20261012T142723996204Z-a50a747-dirty-full-rv32ima-rom8k-l216k-doom-l2-16k-p2 | C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini\build\runs\20261012T161651419425Z-a50a747-dirty-full-rv32ima-rom8k-l216k-doom-fast-arithmetic-p3 |
| abi | a1e130bc | a1e130bc |
| bitstream_sha256 | c3eb06529dbcafbd30289b64cf63c643182abe059f4291285ece8384451a893c | 364728a642ce398b10b4b7be75b7614b14f557ad0c15731d7f401c10d375f078 |
| image_sha256 | 9bf477d5c2da65cf66757f616b89537436234cc24e8adc3649cf4828ddf17a0f | 9b4728fd1c715a29d629d04a3ccb1e74c411a62dcf9e11a76807e9db4c77b82a |

## 配置差异

两项 CPU 算术实现和布局同时变化；保持同频率、缓存和外设配置。

- place_option：`2` → `3`

## 测量中位数

| 项目 | 基线 | 候选 | 数值变化 | 样本数（旧/新） |
| --- | ---: | ---: | ---: | ---: |
| cpu.mul-add | 40.126 cycles/iteration | 8.246 cycles/iteration | -79.5% | 3/3 |
| cpu.xorshift | 45.345 cycles/iteration | 15.463 cycles/iteration | -65.9% | 3/3 |
| io.flash-read (65536 B) | 0.540 MiB/s | 0.582 MiB/s | +7.6% | 3/3 |
| io.rgb-fill16 (261120 B) | 6.395 MiB/s | 6.324 MiB/s | -1.1% | 3/3 |
| io.sd-read (512 B) | 0.562 MiB/s | 0.571 MiB/s | +1.6% | 3/3 |
| io.sd-read (4096 B) | 1.898 MiB/s | 1.923 MiB/s | +1.3% | 3/3 |
| io.uart-tx (4096 B) | 0.011 MiB/s | 0.011 MiB/s | -0.0% | 3/3 |
| mem.chase32 (1048576 B) | 2104.260 ns/load | 2105.957 ns/load | +0.1% | 3/3 |
| mem.copy32 (1024 B) | 25.769 MiB/s | 25.757 MiB/s | -0.0% | 3/3 |
| mem.copy32 (4096 B) | 5.471 MiB/s | 5.500 MiB/s | +0.5% | 3/3 |
| mem.copy32 (65536 B) | 5.354 MiB/s | 5.382 MiB/s | +0.5% | 3/3 |
| mem.copy32 (1048576 B) | 5.361 MiB/s | 5.397 MiB/s | +0.7% | 3/3 |
| mem.libc-copy (1048576 B) | 5.317 MiB/s | 5.334 MiB/s | +0.3% | 3/3 |
| mem.libc-set (1048576 B) | 7.819 MiB/s | 7.812 MiB/s | -0.1% | 3/3 |
| mem.read32 (1024 B) | 20.137 MiB/s | 22.138 MiB/s | +9.9% | 3/3 |
| mem.read32 (4096 B) | 12.929 MiB/s | 13.418 MiB/s | +3.8% | 3/3 |
| mem.read32 (65536 B) | 8.895 MiB/s | 9.185 MiB/s | +3.3% | 3/3 |
| mem.read32 (1048576 B) | 8.910 MiB/s | 9.180 MiB/s | +3.0% | 3/3 |
| mem.write32 (1024 B) | 24.034 MiB/s | 24.048 MiB/s | +0.1% | 3/3 |
| mem.write32 (4096 B) | 24.069 MiB/s | 24.037 MiB/s | -0.1% | 3/3 |
| mem.write32 (65536 B) | 7.841 MiB/s | 7.854 MiB/s | +0.2% | 3/3 |
| mem.write32 (1048576 B) | 7.862 MiB/s | 7.856 MiB/s | -0.1% | 3/3 |

周期/延迟越低越快，MiB/s 越高越快。CPU 指标包含循环控制，不是 MIPS。缓存写入计时不能解释为物理 DDR 写回峰值。JSON 保留每组范围和样本数。

## 并发状态和边界

- LCD underflow baseline：`{'cpu-baseline-all': [0, 0]}`（每个 UART 中的前后计数）。
- LCD underflow candidate：`{'both-p3-all': [0, 0]}`（每个 UART 中的前后计数）。
- Both are ROM8K/L216K/CPU60/DDR120 with identical peripherals; CPU arithmetic implementations and placement differ.
- The cache suite rejects L2 sizes other than 4096 before executing; it is unsupported for both configurations, not a passing cache test.
- Both-enabled place2 passed timing but failed board upload/copy; place3 passed firmware/60s soak/all3/Doom3. The initial failure root is not uniquely identified.
- 未匹配候选项目：`[]`；未匹配基线项目：`[]`。

## 原始数据

- baseline：`C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini\build\doom-perf\cpu-baseline-all\results.json`，SHA256 `97a0745346e49b6b42a1a740e2c7e1e9ebf632a51dc415ee9f5dd6ddafb7ba98`，身份关联：recorded。
- candidate：`C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini\build\doom-perf\both-p3-all\results.json`，SHA256 `a8ea7f973b047b9ce4ec103a46dadad86852e6a6eb5e17ceeba832f13ea48923`，身份关联：recorded。

## Doom 实板对照（本轮基线）

同一不可变 RV32IM/ILP32 Newlib 载荷，DEMO1 singletics，leveltime 16 起测 120 ticks，结束 leveltime 136；每项三轮。

| CPU 选项 | 三轮耗时 ms | 中位数 FPS | 相对本轮基线 |
| --- | --- | ---: | ---: |
| 原始 false/false | 23307, 23307, 23323 | 5.149 | 基线 |
| 仅 Shift | 19503, 19486, 19470 | 6.158 | +19.61% |
| 仅 MulDiv | 22806, 22806, 22789 | 5.262 | +2.20% |
| 两项开启（place3） | 18685, 18685, 18652 | 6.422 | +24.735% |

载荷 `build/doom-benchmark/BOOT.RPB`：541764 bytes，SHA256 `00f02ffaa7415f0b62b21a91f6fd1ea67737792db49a53fb80ce233ce1c58adf`。原始日志 `build/doom-perf-cpu-baseline-round[1..3].log`、`doom-perf-shift-only-round[1..3].log`、`doom-perf-mul-only-round[1..3].log`、`doom-perf-both-p3-round[1..3].log`。比率来自耗时中位数，FPS 为 120000/毫秒。

另行构建的 checksum 验证载荷在计时结束后校验，不混入上述性能样本。原始、Shift、合并版本最终 source=`37fa8643`、palette=`d89868c8`、RGB565 裁剪帧=`f90257a0`，leveltime=136 一致。普通 Doom 抓帧在 `build/doom-cpu-both-p3-normal-frame`。

四项均通过实际 BIOS 启动与完整固件验收；合并 place3 通过 60 秒 soak、三轮 all。初始合并 place2 虽然时序 0/0，却出现实板上传 CRC/复制卡住；该失败未删除，根因仍未唯一定位。这里的成功结论只关联已测 place3 FS=`364728a642ce398b10b4b7be75b7614b14f557ad0c15731d7f401c10d375f078`。`singleCycleMulDiv` 使用流水乘法与迭代除法，不能解释为单周期除法。
