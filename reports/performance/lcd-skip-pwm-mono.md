# 性能测试报告

生成时间：2026-10-14T07:37:41+00:00。数值来自已保存测量，不表示本命令重新跑板。

## 明确选择的版本

| 项目 | 基线 | 候选 |
| --- | --- | --- |
| id | v0.10.0-full-rv32ima-rom8k-l28k-commit-snapshot-ade90f94-f9136d1a | 2d8e6d0-full-rv32ima-rom8k-l28k-lcd-pwm-versioned-mono-546c595a-959c261b |
| path | C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini\build\archives\v0.10.0-2d8e6d0532d8\full-rv32ima-rom8k-l28k | C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini\build\runs\20261014T042810038293Z-2d8e6d0-dirty-full-rv32ima-rom8k-l28k-lcd-pwm-versioned-mono |
| abi | b881c174 | 28890b7e |
| bitstream_sha256 | ade90f94b6ac6f9956d2cfc3bece7df5d8348fcb9e75b3ee6da1c2e2aef19f3a | 546c595ad5192007c2677043c3dacf042297c88bb9142057d5cf92e9eb074631 |
| image_sha256 | 906e1a6e20decc6c5f6648ac88ade0690211cf4b8b19c4a62b235c2ab7bc954e | 5995cb04a8079ac054e8f0a1fc8f573a65459ab8f566b2718162a34133a7ef5f |

## 配置差异

以下差异同时存在，变化百分比不能单独归因于 C 或 fence。

- features：`{'mmu': True, 'fpu': False, 'flash': True, 'spi_lcd': True, 'sd': True, 'filesystem': True, 'video': True, 'board_io': True, 'ws2812': True, 'audio': True, 'mic': True, 'mic_stereo': True, 'eth': True, 'eth_dma': True, 'usb': True}` → `{'mmu': True, 'fpu': False, 'flash': True, 'spi_lcd': True, 'sd': True, 'filesystem': True, 'video': True, 'board_io': True, 'ws2812': True, 'audio': True, 'mic': True, 'mic_stereo': False, 'eth': True, 'eth_dma': True, 'usb': True}`

## 测量中位数

| 项目 | 基线 | 候选 | 数值变化 | 样本数（旧/新） |
| --- | ---: | ---: | ---: | ---: |
| cpu.mul-add | 8.196 cycles/iteration | 8.202 cycles/iteration | +0.1% | 3/3 |
| cpu.xorshift | 15.316 cycles/iteration | 15.365 cycles/iteration | +0.3% | 3/3 |
| io.flash-read (65536 B) | 0.580 MiB/s | 0.578 MiB/s | -0.3% | 3/3 |
| io.rgb-fill16 (261120 B) | 6.479 MiB/s | 6.373 MiB/s | -1.6% | 3/3 |
| io.sd-read (512 B) | 0.538 MiB/s | 0.542 MiB/s | +0.8% | 3/3 |
| io.sd-read (4096 B) | 1.831 MiB/s | 1.816 MiB/s | -0.8% | 3/3 |
| io.uart-tx (4096 B) | 0.011 MiB/s | 0.011 MiB/s | -0.0% | 3/3 |
| mem.chase32 (1048576 B) | 2122.127 ns/load | 2115.002 ns/load | -0.3% | 3/3 |
| mem.copy32 (1024 B) | 25.435 MiB/s | 25.245 MiB/s | -0.7% | 3/3 |
| mem.copy32 (4096 B) | 5.457 MiB/s | 5.412 MiB/s | -0.8% | 3/3 |
| mem.copy32 (65536 B) | 5.462 MiB/s | 5.392 MiB/s | -1.3% | 3/3 |
| mem.copy32 (1048576 B) | 5.468 MiB/s | 5.392 MiB/s | -1.4% | 3/3 |
| mem.libc-copy (1048576 B) | 5.373 MiB/s | 5.332 MiB/s | -0.8% | 3/3 |
| mem.libc-set (1048576 B) | 8.646 MiB/s | 8.584 MiB/s | -0.7% | 3/3 |
| mem.read32 (1024 B) | 22.134 MiB/s | 22.136 MiB/s | +0.0% | 3/3 |
| mem.read32 (4096 B) | 13.185 MiB/s | 13.161 MiB/s | -0.2% | 3/3 |
| mem.read32 (65536 B) | 9.101 MiB/s | 9.042 MiB/s | -0.7% | 3/3 |
| mem.read32 (1048576 B) | 9.105 MiB/s | 9.044 MiB/s | -0.7% | 3/3 |
| mem.write32 (1024 B) | 23.843 MiB/s | 23.824 MiB/s | -0.1% | 3/3 |
| mem.write32 (4096 B) | 23.613 MiB/s | 23.566 MiB/s | -0.2% | 3/3 |
| mem.write32 (65536 B) | 8.750 MiB/s | 8.706 MiB/s | -0.5% | 3/3 |
| mem.write32 (1048576 B) | 8.755 MiB/s | 8.705 MiB/s | -0.6% | 3/3 |

周期/延迟越低越快，MiB/s 越高越快。CPU 指标包含循环控制，不是 MIPS。缓存写入计时不能解释为物理 DDR 写回峰值。JSON 保留每组范围和样本数。

## 并发状态和边界

- LCD underflow baseline：`{'all': [0, 0]}`（每个 UART 中的前后计数）。
- LCD underflow candidate：`{'all': [0, 0]}`（每个 UART 中的前后计数）。
- Both builds use 8 KiB L2; bench cache rejects this size because it requires 4 KiB L2. Failed baseline cache log is preserved at build/reports/performance/20261014-lcd-pwm-commit/baseline/cache/uart.log; cache suite is excluded.
- Comparison covers LCD skip/PWM and single microphone configuration; all uses normal full-screen scanout, so this is a regression check, not a Doom zero-copy FPS measurement.
- Worst setup slack is +0.031 ns. Physical backlight brightness, audible output and external network throughput were not observed.
- 未匹配候选项目：`[]`；未匹配基线项目：`[]`。

## 原始数据

- baseline：`C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini\build\archives\v0.10.0-2d8e6d0532d8\full-rv32ima-rom8k-l28k\reports\performance\candidate\all\results.json`，SHA256 `930090def504a2e79a9787416fc22083a40ccd5d4da70f7f81cf39389652d656`，身份关联：recorded。
- candidate：`C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini\build\reports\performance\20261014-lcd-pwm-commit\candidate\all\results.json`，SHA256 `2ed4eff32670d466716c2393601110eaa12ade7ce4044431dcac025723592530`，身份关联：recorded。
