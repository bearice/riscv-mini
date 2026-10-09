# 性能测试报告

生成时间：2026-10-07T05:57:08+00:00。数值来自已保存测量，不表示本命令重新跑板。

## 明确选择的版本

| 项目 | 基线 | 候选 |
| --- | --- | --- |
| id | v0.7.1-full-rv32imafc-rom4k-l24k-code-release-621823d2-123ceedf | da9a3d8-full-rv32imafc-rom4k-l24k-buffered-memory-ports-57060960-709ddac8 |
| path | C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini\build\releases\v0.7.1\full-rv32imafc-rom4k-l24k | C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini\build\worktrees\memory-ports\build\buffered-ports-final |
| abi | ba18270e | ba18270e |
| bitstream_sha256 | 621823d2261fa44e2cf07970d0763b6de4b9441b174de93bb4f9ebda780a0e1a | 570609605ae7984068ee68a512525aac840a7c256348358c990804e1562f9d2c |
| image_sha256 | 2b65ca0201b33e5d3573493125a98e48596d317c88cc9f07ff9c0d9c8baa9006 | ad4dbd4338e3ed8b47596a5f933ff0bcb6ea192695d9f558a0088c67c69dc998 |

## 配置差异

以下差异同时存在，变化百分比不能单独归因于 C 或 fence。


## 测量中位数

| 项目 | 基线 | 候选 | 数值变化 | 样本数（旧/新） |
| --- | ---: | ---: | ---: | ---: |
| cache.read-conflict | 112.879 cycles/access | 111.760 cycles/access | -1.0% | 3/3 |
| cache.read-l1 | 3.260 cycles/access | 3.296 cycles/access | +1.1% | 3/3 |
| cache.read-l2 | 55.159 cycles/access | 55.175 cycles/access | +0.0% | 3/3 |
| cache.read-l2-off | 274.034 cycles/access | 270.746 cycles/access | -1.2% | 3/3 |
| cache.write-l1-conflict | 7.493 cycles/access | 7.472 cycles/access | -0.3% | 3/3 |
| cache.write-l1-hot | 7.493 cycles/access | 7.474 cycles/access | -0.3% | 3/3 |
| cache.write-l2-conflict | 60.295 cycles/access | 60.295 cycles/access | +0.0% | 3/3 |
| cache.write-l2-off | 33.219 cycles/access | 33.031 cycles/access | -0.6% | 3/3 |
| cpu.fadd-dependent | 9.158 cycles/iteration | 9.174 cycles/iteration | +0.2% | 3/3 |
| cpu.mul-add | 39.718 cycles/iteration | 39.761 cycles/iteration | +0.1% | 3/3 |
| cpu.xorshift | 44.781 cycles/iteration | 44.860 cycles/iteration | +0.2% | 3/3 |
| io.flash-read (65536 B) | 0.527 MiB/s | 0.525 MiB/s | -0.4% | 3/3 |
| io.rgb-fill16 (261120 B) | 3.626 MiB/s | 3.653 MiB/s | +0.7% | 3/3 |
| io.sd-read (512 B) | 0.596 MiB/s | 0.615 MiB/s | +3.1% | 3/3 |
| io.sd-read (4096 B) | 1.888 MiB/s | 1.935 MiB/s | +2.5% | 3/3 |
| io.uart-tx (4096 B) | 0.011 MiB/s | 0.011 MiB/s | -0.0% | 3/3 |
| mem.chase32 (1048576 B) | 2130.086 ns/load | 2114.229 ns/load | -0.7% | 3/3 |
| mem.copy32 (1024 B) | 23.723 MiB/s | 23.814 MiB/s | +0.4% | 3/3 |
| mem.copy32 (4096 B) | 5.282 MiB/s | 5.348 MiB/s | +1.2% | 3/3 |
| mem.copy32 (65536 B) | 5.325 MiB/s | 5.388 MiB/s | +1.2% | 3/3 |
| mem.copy32 (1048576 B) | 5.329 MiB/s | 5.389 MiB/s | +1.1% | 3/3 |
| mem.libc-copy (1048576 B) | 5.165 MiB/s | 5.228 MiB/s | +1.2% | 3/3 |
| mem.libc-set (1048576 B) | 8.704 MiB/s | 8.566 MiB/s | -1.6% | 3/3 |
| mem.read32 (1024 B) | 18.338 MiB/s | 18.468 MiB/s | +0.7% | 3/3 |
| mem.read32 (4096 B) | 11.431 MiB/s | 11.434 MiB/s | +0.0% | 3/3 |
| mem.read32 (65536 B) | 8.312 MiB/s | 8.302 MiB/s | -0.1% | 3/3 |
| mem.read32 (1048576 B) | 8.318 MiB/s | 8.305 MiB/s | -0.1% | 3/3 |
| mem.write32 (1024 B) | 20.549 MiB/s | 20.653 MiB/s | +0.5% | 3/3 |
| mem.write32 (4096 B) | 19.432 MiB/s | 19.626 MiB/s | +1.0% | 3/3 |
| mem.write32 (65536 B) | 8.689 MiB/s | 8.572 MiB/s | -1.3% | 3/3 |
| mem.write32 (1048576 B) | 8.688 MiB/s | 8.571 MiB/s | -1.3% | 3/3 |

周期/延迟越低越快，MiB/s 越高越快。CPU 指标包含循环控制，不是 MIPS。缓存写入计时不能解释为物理 DDR 写回峰值。JSON 保留每组范围和样本数。

## 并发状态和边界

- LCD underflow baseline：`{'all': [0, 0], 'cache': [0, 27]}`（每个 UART 中的前后计数）。
- LCD underflow candidate：`{'all': [0, 0], 'cache': [0, 27]}`（每个 UART 中的前后计数）。
- 基线使用 v0.7.1 已保存三轮实板数据，候选 BIOS 为基于 da9a3d8 的 v0.7.2；两版 benchmark.c 与 firmware/hal 无差异，但整体固件布局不同，因此数值为版本对比，不能完全归因于端口缓冲。
- CPU 输入 RTL、sys 60 MHz / DDR CK 120 MHz、4 KiB ROM/L2、SD lite、USB ultra、DDS audio 相同；没有补测基线或运行网络专用基准。
- 候选另行通过 60 秒 SD/LCD/USB/audio 并发 soak：42 轮，LCD underflow、audio underrun/error 为 0。没有主机听音或屏幕人工观察。
- 未匹配候选项目：`[]`；未匹配基线项目：`[]`。

## 原始数据

- baseline：`C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini\build\releases\v0.7.1\full-rv32imafc-rom4k-l24k\reports\performance\candidate\all\results.json`，SHA256 `fcfe2d1f164934e530e34e906cbbae792c1146edc7cbffe7808e756c305d1d95`，身份关联：recorded。
- baseline：`C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini\build\releases\v0.7.1\full-rv32imafc-rom4k-l24k\reports\performance\candidate\cache\results.json`，SHA256 `caa272dc2891361718416b64ae7e054bde68612034efe9ad8ad175bd7cb89e39`，身份关联：recorded。
- candidate：`C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini\build\worktrees\memory-ports\build\reports\performance\buffered-ports\all\results.json`，SHA256 `a22dad07699446d6fccf80e4e5cbab9f628d24633930de3349f6dd2da26261cd`，身份关联：recorded。
- candidate：`C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini\build\worktrees\memory-ports\build\reports\performance\buffered-ports\cache\results.json`，SHA256 `6df9e6df8d921e5854a4837fc671ebdc25a0c3439e61477ac927989af0b221cd`，身份关联：recorded。
