# 性能测试报告

生成时间：2026-10-10T13:30:33+00:00。数值来自已保存测量，不表示本命令重新跑板。

## 明确选择的版本

| 项目 | 基线 | 候选 |
| --- | --- | --- |
| id | v0.9.1-full-rv32ima-rom0k-l24k-code-release-cc3d7d13-72d07134 | 45398e9-full-rv32ima-rom0k-l24k-linux-usb-commit-31595fad-ee74c4ff |
| path | C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini\build\releases\system\v0.9.1\full-rv32ima-rom0k-l24k | C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini\build\runs\20261010T132235828694Z-45398e9-dirty-full-rv32ima-rom0k-l24k-linux-usb-commit |
| abi | 71da004d | 71da004d |
| bitstream_sha256 | cc3d7d131510ae05d472f589bd2bcaea99eaee824b17cf95bd0e3221b6fe76e4 | 31595fad5470cdbdf616d3114a9725e42ea09621517807269ab7b77ad57fce24 |
| image_sha256 | 3b4a0b5baeb210f1d0b16d5f825e161218ed6a40115f7e887a928f4111390700 | dd338308d65be07b6b20bb4912004b050b3219ea67cf0918bbd3f678bb464fdd |

## 配置差异

以下差异同时存在，变化百分比不能单独归因于 C 或 fence。


## 测量中位数

| 项目 | 基线 | 候选 | 数值变化 | 样本数（旧/新） |
| --- | ---: | ---: | ---: | ---: |
| cache.read-conflict | 112.142 cycles/access | 112.048 cycles/access | -0.1% | 3/3 |
| cache.read-l1 | 3.420 cycles/access | 3.397 cycles/access | -0.7% | 3/3 |
| cache.read-l2 | 54.966 cycles/access | 55.256 cycles/access | +0.5% | 3/3 |
| cache.read-l2-off | 274.207 cycles/access | 274.000 cycles/access | -0.1% | 3/3 |
| cache.write-l1-conflict | 7.512 cycles/access | 7.515 cycles/access | +0.0% | 3/3 |
| cache.write-l1-hot | 7.486 cycles/access | 7.474 cycles/access | -0.2% | 3/3 |
| cache.write-l2-conflict | 59.697 cycles/access | 59.710 cycles/access | +0.0% | 3/3 |
| cache.write-l2-off | 33.481 cycles/access | 33.353 cycles/access | -0.4% | 3/3 |
| cpu.mul-add | 40.525 cycles/iteration | 40.226 cycles/iteration | -0.7% | 3/3 |
| cpu.xorshift | 44.986 cycles/iteration | 44.991 cycles/iteration | +0.0% | 3/3 |
| io.flash-read (65536 B) | 0.544 MiB/s | 0.544 MiB/s | -0.1% | 3/3 |
| io.rgb-fill16 (261120 B) | 6.325 MiB/s | 6.336 MiB/s | +0.2% | 3/3 |
| io.sd-read (512 B) | 0.493 MiB/s | 0.496 MiB/s | +0.7% | 3/3 |
| io.sd-read (4096 B) | 1.752 MiB/s | 1.754 MiB/s | +0.1% | 3/3 |
| io.uart-tx (4096 B) | 0.011 MiB/s | 0.011 MiB/s | -0.0% | 3/3 |
| mem.chase32 (1048576 B) | 2119.005 ns/load | 2121.613 ns/load | +0.1% | 3/3 |
| mem.copy32 (1024 B) | 25.545 MiB/s | 25.537 MiB/s | -0.0% | 3/3 |
| mem.copy32 (4096 B) | 5.334 MiB/s | 5.332 MiB/s | -0.0% | 3/3 |
| mem.copy32 (65536 B) | 5.386 MiB/s | 5.384 MiB/s | -0.0% | 3/3 |
| mem.copy32 (1048576 B) | 5.388 MiB/s | 5.386 MiB/s | -0.0% | 3/3 |
| mem.libc-copy (1048576 B) | 5.279 MiB/s | 5.276 MiB/s | -0.0% | 3/3 |
| mem.libc-set (1048576 B) | 8.496 MiB/s | 8.504 MiB/s | +0.1% | 3/3 |
| mem.read32 (1024 B) | 20.146 MiB/s | 20.145 MiB/s | -0.0% | 3/3 |
| mem.read32 (4096 B) | 12.209 MiB/s | 12.210 MiB/s | +0.0% | 3/3 |
| mem.read32 (65536 B) | 8.751 MiB/s | 8.742 MiB/s | -0.1% | 3/3 |
| mem.read32 (1048576 B) | 8.752 MiB/s | 8.747 MiB/s | -0.1% | 3/3 |
| mem.write32 (1024 B) | 23.928 MiB/s | 23.928 MiB/s | -0.0% | 3/3 |
| mem.write32 (4096 B) | 22.403 MiB/s | 22.397 MiB/s | -0.0% | 3/3 |
| mem.write32 (65536 B) | 8.645 MiB/s | 8.649 MiB/s | +0.0% | 3/3 |
| mem.write32 (1048576 B) | 8.635 MiB/s | 8.653 MiB/s | +0.2% | 3/3 |

周期/延迟越低越快，MiB/s 越高越快。CPU 指标包含循环控制，不是 MIPS。缓存写入计时不能解释为物理 DDR 写回峰值。JSON 保留每组范围和样本数。

## 并发状态和边界

- LCD underflow baseline：`{'all': [0, 0], 'cache': [0, 24]}`（每个 UART 中的前后计数）。
- LCD underflow candidate：`{'all': [0, 0], 'cache': [0, 24]}`（每个 UART 中的前后计数）。
- Same 60/120 MHz RV32IMA no-C/no-FPU full configuration; ordinary commit candidate includes USB PHY and BIOS fixes. Measurements are BIOS workloads, not Linux performance or USB throughput.
- Linux native qualification passed after one earlier ALSA capture-read failure; intermittent capture stability remains unresolved. Physical screen color, audio listening and prolonged hotplug were not qualified.
- 未匹配候选项目：`[]`；未匹配基线项目：`[]`。

## 原始数据

- baseline：`C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini\build\reports\performance\linux-usb-commit\baseline\all\results.json`，SHA256 `cb5eaf201e59025edf96845e96753ed8366a834488cab255e1c6876a2a3593f1`，身份关联：recorded。
- baseline：`C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini\build\reports\performance\linux-usb-commit\baseline\cache\results.json`，SHA256 `030f02caae9e7df41e1de9a3c4a8340a698753213c15a25536a78387a81ab39a`，身份关联：recorded。
- candidate：`C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini\build\reports\performance\linux-usb-commit\candidate\all\results.json`，SHA256 `08d254a3f6d7d69ef60b1b67bddb5c54489f2506c8dfe06abb415e0951c0b437`，身份关联：recorded。
- candidate：`C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini\build\reports\performance\linux-usb-commit\candidate\cache\results.json`，SHA256 `26f5388487dfb84de1bf8ae184f3b3ffc303c4cddd0844cb2ba60ad66792e1d8`，身份关联：recorded。
