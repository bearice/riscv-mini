# 性能测试报告

生成时间：2026-10-07T09:44:38+00:00。数值来自已保存测量，不表示本命令重新跑板。

## 明确选择的版本

| 项目 | 基线 | 候选 |
| --- | --- | --- |
| id | da9a3d8-full-rv32imafc-rom4k-l24k-no-audio-control-42e03fbd-f3fcae6c | da9a3d8-full-rv32imafc-rom4k-l24k-eth-dma-no-audio-1065bc87-2b4b1edd |
| path | C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini\build\worktrees\eth-dma\build\no-audio-control | C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini\build\worktrees\eth-dma\build\eth-dma-final |
| abi | 6f31b306 | 0e0b5dbc |
| bitstream_sha256 | 42e03fbdb44019722fb2ab15e2ab3f96dbac71a1ca068e6737f7ca3361bb8ed3 | 1065bc878659b91549cbcf6c52f9c585d3c5bf0f0bbd38e8339d4fd052017d14 |
| image_sha256 | 0ea67c9ce6729eb8395319c2d8892c96083fa981186e80941ba77366d5cef8c8 | 133f79aa373e876ea8d77a0517797a1f42c4b9f1213257f641f7449dd4d40cff |

## 配置差异

以下差异同时存在，变化百分比不能单独归因于 C 或 fence。

- features：`{'mmu': True, 'fpu': True, 'flash': True, 'spi_lcd': True, 'sd': True, 'filesystem': True, 'video': True, 'board_io': True, 'ws2812': True, 'audio': False, 'mic': False, 'mic_stereo': False, 'eth': True, 'eth_dma': False, 'usb': True}` → `{'mmu': True, 'fpu': True, 'flash': True, 'spi_lcd': True, 'sd': True, 'filesystem': True, 'video': True, 'board_io': True, 'ws2812': True, 'audio': False, 'mic': False, 'mic_stereo': False, 'eth': True, 'eth_dma': True, 'usb': True}`

## 测量中位数

| 项目 | 基线 | 候选 | 数值变化 | 样本数（旧/新） |
| --- | ---: | ---: | ---: | ---: |
| cache.read-conflict | 111.759 cycles/access | 111.786 cycles/access | +0.0% | 3/3 |
| cache.read-l1 | 3.260 cycles/access | 3.262 cycles/access | +0.1% | 3/3 |
| cache.read-l2 | 55.062 cycles/access | 55.137 cycles/access | +0.1% | 3/3 |
| cache.read-l2-off | 272.592 cycles/access | 273.967 cycles/access | +0.5% | 3/3 |
| cache.write-l1-conflict | 7.484 cycles/access | 7.481 cycles/access | -0.0% | 3/3 |
| cache.write-l1-hot | 7.487 cycles/access | 7.483 cycles/access | -0.0% | 3/3 |
| cache.write-l2-conflict | 60.255 cycles/access | 60.200 cycles/access | -0.1% | 3/3 |
| cache.write-l2-off | 33.202 cycles/access | 33.173 cycles/access | -0.1% | 3/3 |
| cpu.fadd-dependent | 9.187 cycles/iteration | 9.321 cycles/iteration | +1.5% | 3/3 |
| cpu.mul-add | 40.789 cycles/iteration | 41.436 cycles/iteration | +1.6% | 3/3 |
| cpu.xorshift | 44.851 cycles/iteration | 45.577 cycles/iteration | +1.6% | 3/3 |
| io.flash-read (65536 B) | 0.538 MiB/s | 0.519 MiB/s | -3.6% | 3/3 |
| io.rgb-fill16 (261120 B) | 3.633 MiB/s | 3.550 MiB/s | -2.3% | 3/3 |
| io.sd-read (512 B) | 0.603 MiB/s | 0.616 MiB/s | +2.1% | 3/3 |
| io.sd-read (4096 B) | 1.934 MiB/s | 1.932 MiB/s | -0.1% | 3/3 |
| io.uart-tx (4096 B) | 0.011 MiB/s | 0.011 MiB/s | +0.0% | 3/3 |
| mem.chase32 (1048576 B) | 2122.693 ns/load | 2145.688 ns/load | +1.1% | 3/3 |
| mem.copy32 (1024 B) | 23.694 MiB/s | 25.134 MiB/s | +6.1% | 3/3 |
| mem.copy32 (4096 B) | 5.353 MiB/s | 5.325 MiB/s | -0.5% | 3/3 |
| mem.copy32 (65536 B) | 5.385 MiB/s | 5.368 MiB/s | -0.3% | 3/3 |
| mem.copy32 (1048576 B) | 5.387 MiB/s | 5.369 MiB/s | -0.3% | 3/3 |
| mem.libc-copy (1048576 B) | 5.344 MiB/s | 5.192 MiB/s | -2.8% | 3/3 |
| mem.libc-set (1048576 B) | 8.695 MiB/s | 8.509 MiB/s | -2.1% | 3/3 |
| mem.read32 (1024 B) | 18.222 MiB/s | 18.113 MiB/s | -0.6% | 3/3 |
| mem.read32 (4096 B) | 11.248 MiB/s | 11.362 MiB/s | +1.0% | 3/3 |
| mem.read32 (65536 B) | 8.249 MiB/s | 8.281 MiB/s | +0.4% | 3/3 |
| mem.read32 (1048576 B) | 8.263 MiB/s | 8.295 MiB/s | +0.4% | 3/3 |
| mem.write32 (1024 B) | 20.214 MiB/s | 23.505 MiB/s | +16.3% | 3/3 |
| mem.write32 (4096 B) | 18.841 MiB/s | 22.124 MiB/s | +17.4% | 3/3 |
| mem.write32 (65536 B) | 8.512 MiB/s | 8.623 MiB/s | +1.3% | 3/3 |
| mem.write32 (1048576 B) | 8.496 MiB/s | 8.641 MiB/s | +1.7% | 3/3 |

周期/延迟越低越快，MiB/s 越高越快。CPU 指标包含循环控制，不是 MIPS。缓存写入计时不能解释为物理 DDR 写回峰值。JSON 保留每组范围和样本数。

## 并发状态和边界

- LCD underflow baseline：`{'all': [0, 0], 'cache': [0, 27]}`（每个 UART 中的前后计数）。
- LCD underflow candidate：`{'all': [0, 0], 'cache': [0, 26]}`（每个 UART 中的前后计数）。
- Wishbone copy DMA comparison only; current branch uses native DDR ports and failed SD board acceptance. This report does not qualify the native implementation or measure network throughput.
- 未匹配候选项目：`[]`；未匹配基线项目：`[]`。

## 原始数据

- baseline：`C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini\build\worktrees\eth-dma\build\reports\performance\no-audio-control\all\results.json`，SHA256 `362a1acd2299580a070ba8c81ac0baa160a0a514e7d2b570640bf0632ff87597`，身份关联：recorded。
- baseline：`C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini\build\worktrees\eth-dma\build\reports\performance\no-audio-control\cache\results.json`，SHA256 `b7b187de5cb8ee41e744c76faa1a40db7f6bf435cc0648e3582fb61ba9f30f36`，身份关联：recorded。
- candidate：`C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini\build\worktrees\eth-dma\build\reports\performance\eth-dma\all\results.json`，SHA256 `17a2eb3b37c4b2e355b946937a72aef556aba49f6c22a808555066789df6ba50`，身份关联：recorded。
- candidate：`C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini\build\worktrees\eth-dma\build\reports\performance\eth-dma\cache\results.json`，SHA256 `b9d234a2e1fa8793a5852b8344333b5366b03ea887742c158ae2f926f47b189a`，身份关联：recorded。
