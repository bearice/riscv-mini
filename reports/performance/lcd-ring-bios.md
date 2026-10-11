# 性能测试报告

生成时间：2026-10-11T03:42:48+00:00。数值来自已保存测量，不表示本命令重新跑板。

## 明确选择的版本

| 项目 | 基线 | 候选 |
| --- | --- | --- |
| id | v0.9.1-full-rv32ima-rom0k-l24k-commit-snapshot-9f9f56ce-cd643d45 | a4d09c0-full-rv32ima-rom0k-l24k-lcd-ring-commit-82c71ed6-461bdd84 |
| path | C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini\build\archives\v0.9.1-2cdec4723183\full-rv32ima-rom0k-l24k | C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini\build\runs\20261011T033421721618Z-a4d09c0-dirty-full-rv32ima-rom0k-l24k-lcd-ring-commit |
| abi | 71da004d | 71da004d |
| bitstream_sha256 | 9f9f56ce7d01e99a2df0af9cc428c46a4515a3a384f1852eb0066b29c67c168e | 82c71ed662f07e5e04566c83f0b4b16012ea9c27c615a5db2e0e0dce91b59b66 |
| image_sha256 | 7e25d29ff9e9774934012f71372710e22f087ca54c6f1cbfe3fca2224d5975a9 | afa71581343771d2b6035226ad492c85c18fb0802d6939424d2c56b322ef27ce |

## 配置差异

以下差异同时存在，变化百分比不能单独归因于 C 或 fence。


## 测量中位数

| 项目 | 基线 | 候选 | 数值变化 | 样本数（旧/新） |
| --- | ---: | ---: | ---: | ---: |
| cache.read-conflict | 112.006 cycles/access | 112.090 cycles/access | +0.1% | 3/3 |
| cache.read-l1 | 3.397 cycles/access | 3.391 cycles/access | -0.2% | 3/3 |
| cache.read-l2 | 54.950 cycles/access | 54.940 cycles/access | -0.0% | 3/3 |
| cache.read-l2-off | 274.092 cycles/access | 274.039 cycles/access | -0.0% | 3/3 |
| cache.write-l1-conflict | 7.536 cycles/access | 7.535 cycles/access | -0.0% | 3/3 |
| cache.write-l1-hot | 7.487 cycles/access | 7.486 cycles/access | -0.0% | 3/3 |
| cache.write-l2-conflict | 59.710 cycles/access | 59.710 cycles/access | -0.0% | 3/3 |
| cache.write-l2-off | 33.497 cycles/access | 33.479 cycles/access | -0.1% | 3/3 |
| cpu.mul-add | 40.238 cycles/iteration | 40.215 cycles/iteration | -0.1% | 3/3 |
| cpu.xorshift | 45.007 cycles/iteration | 44.992 cycles/iteration | -0.0% | 3/3 |
| io.flash-read (65536 B) | 0.544 MiB/s | 0.544 MiB/s | +0.0% | 3/3 |
| io.rgb-fill16 (261120 B) | 6.355 MiB/s | 6.351 MiB/s | -0.1% | 3/3 |
| io.sd-read (512 B) | 0.494 MiB/s | 0.494 MiB/s | -0.1% | 3/3 |
| io.sd-read (4096 B) | 1.758 MiB/s | 1.758 MiB/s | +0.0% | 3/3 |
| io.uart-tx (4096 B) | 0.011 MiB/s | 0.011 MiB/s | -0.0% | 3/3 |
| mem.chase32 (1048576 B) | 2117.867 ns/load | 2117.595 ns/load | -0.0% | 3/3 |
| mem.copy32 (1024 B) | 25.540 MiB/s | 25.558 MiB/s | +0.1% | 3/3 |
| mem.copy32 (4096 B) | 5.342 MiB/s | 5.355 MiB/s | +0.2% | 3/3 |
| mem.copy32 (65536 B) | 5.385 MiB/s | 5.390 MiB/s | +0.1% | 3/3 |
| mem.copy32 (1048576 B) | 5.387 MiB/s | 5.383 MiB/s | -0.1% | 3/3 |
| mem.libc-copy (1048576 B) | 5.285 MiB/s | 5.311 MiB/s | +0.5% | 3/3 |
| mem.libc-set (1048576 B) | 8.539 MiB/s | 8.568 MiB/s | +0.3% | 3/3 |
| mem.read32 (1024 B) | 20.128 MiB/s | 20.143 MiB/s | +0.1% | 3/3 |
| mem.read32 (4096 B) | 12.214 MiB/s | 12.273 MiB/s | +0.5% | 3/3 |
| mem.read32 (65536 B) | 8.759 MiB/s | 8.783 MiB/s | +0.3% | 3/3 |
| mem.read32 (1048576 B) | 8.775 MiB/s | 8.796 MiB/s | +0.2% | 3/3 |
| mem.write32 (1024 B) | 23.919 MiB/s | 23.929 MiB/s | +0.0% | 3/3 |
| mem.write32 (4096 B) | 22.440 MiB/s | 22.411 MiB/s | -0.1% | 3/3 |
| mem.write32 (65536 B) | 8.667 MiB/s | 8.694 MiB/s | +0.3% | 3/3 |
| mem.write32 (1048576 B) | 8.676 MiB/s | 8.700 MiB/s | +0.3% | 3/3 |

周期/延迟越低越快，MiB/s 越高越快。CPU 指标包含循环控制，不是 MIPS。缓存写入计时不能解释为物理 DDR 写回峰值。JSON 保留每组范围和样本数。

## 并发状态和边界

- LCD underflow baseline：`{'all': [0, 0], 'cache': [0, 24]}`（每个 UART 中的前后计数）。
- LCD underflow candidate：`{'all': [0, 0], 'cache': [0, 24]}`（每个 UART 中的前后计数）。
- BIOS all/cache 在连续扫描模式测量，Linux 回卷滚屏单独记录在 lcd-ring-console.md。
- 不包含听音、物理 LCD 目视或实际键盘按键验收。
- 未匹配候选项目：`[]`；未匹配基线项目：`[]`。

## 原始数据

- baseline：`C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini\build\archives\v0.9.1-2cdec4723183\full-rv32ima-rom0k-l24k\reports\performance\candidate\all\results.json`，SHA256 `8599b9ca0cd97ba4bc559ae1c727bc0b5f9ea8b9bdfd97648da7b931622d0126`，身份关联：recorded。
- baseline：`C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini\build\archives\v0.9.1-2cdec4723183\full-rv32ima-rom0k-l24k\reports\performance\candidate\cache\results.json`，SHA256 `104e9918d489c189ad352a77ad7d714f19513accbce5e8ef6b677692ac3689cf`，身份关联：recorded。
- candidate：`C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini\build\reports\performance\20261011-lcd-ring-commit\all\results.json`，SHA256 `452142d8bc3d719aa3a2da1a75c213d4f015455c000892a3fe02a90ea37d0e7f`，身份关联：recorded。
- candidate：`C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini\build\reports\performance\20261011-lcd-ring-commit\cache\results.json`，SHA256 `3907538095a1655f1b482d03c019b2ac50b89e8e4d762b611352510751724501`，身份关联：recorded。
