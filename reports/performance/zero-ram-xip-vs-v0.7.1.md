# 性能测试报告

生成时间：2026-10-08T02:51:15+00:00。数值来自已保存测量，不表示本命令重新跑板。

## 明确选择的版本

| 项目 | 基线 | 候选 |
| --- | --- | --- |
| id | ce277e0-full-rv32imafc-rom4k-l24k-baseline-qualified-c-rom-621823d2 | da9a3d8-full-rv32imaf-rom0k-l24k-merge-reviewed-f86edd8b-7d944190 |
| path | C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini\build\releases\v0.7.1\full-rv32imafc-rom4k-l24k | C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini\build\worktrees\noc-flash-xip\build\runs\20261008T024452065729Z-da9a3d8-dirty-full-rv32imaf-rom0k-l24k-merge-reviewed |
| abi | ba18270e | 038c2988 |
| bitstream_sha256 | 621823d2261fa44e2cf07970d0763b6de4b9441b174de93bb4f9ebda780a0e1a | f86edd8bf68049e8ca119df0d27c70f649722e30554fd337b7f713c7eb3e29d6 |
| image_sha256 | 2b65ca0201b33e5d3573493125a98e48596d317c88cc9f07ff9c0d9c8baa9006 | 07e02047401cab04ddb4360c7cfb2ad8dbe1542242eb5002f110b849abc18226 |

## 配置差异

以下差异同时存在，变化百分比不能单独归因于 C 或 fence。

- isa：`rv32imafc_zicsr_zifencei` → `rv32imaf_zicsr_zifencei`
- rom_size_bytes：`4096` → `0`
- cpu_capabilities：`{'mmu': True, 'fpu': True, 'dcache': True, 'compressed': True, 'bitmanip': []}` → `{'mmu': True, 'fpu': True, 'dcache': True, 'compressed': False, 'bitmanip': [], 'high_mmio_xip': True}`
- place_option：`2` → `3`

## 测量中位数

| 项目 | 基线 | 候选 | 数值变化 | 样本数（旧/新） |
| --- | ---: | ---: | ---: | ---: |
| cache.read-conflict | 112.879 cycles/access | 112.878 cycles/access | -0.0% | 3/3 |
| cache.read-l1 | 3.260 cycles/access | 3.292 cycles/access | +1.0% | 3/3 |
| cache.read-l2 | 55.159 cycles/access | 55.216 cycles/access | +0.1% | 3/3 |
| cache.read-l2-off | 274.034 cycles/access | 274.157 cycles/access | +0.0% | 3/3 |
| cache.write-l1-conflict | 7.493 cycles/access | 7.491 cycles/access | -0.0% | 3/3 |
| cache.write-l1-hot | 7.493 cycles/access | 7.542 cycles/access | +0.6% | 3/3 |
| cache.write-l2-conflict | 60.295 cycles/access | 59.965 cycles/access | -0.5% | 3/3 |
| cache.write-l2-off | 33.219 cycles/access | 33.176 cycles/access | -0.1% | 3/3 |
| cpu.fadd-dependent | 9.158 cycles/iteration | 9.227 cycles/iteration | +0.7% | 3/3 |
| cpu.mul-add | 39.718 cycles/iteration | 39.995 cycles/iteration | +0.7% | 3/3 |
| cpu.xorshift | 44.781 cycles/iteration | 45.096 cycles/iteration | +0.7% | 3/3 |
| io.flash-read (65536 B) | 0.527 MiB/s | 0.520 MiB/s | -1.4% | 3/3 |
| io.rgb-fill16 (261120 B) | 3.626 MiB/s | 6.506 MiB/s | +79.4% | 3/3 |
| io.sd-read (512 B) | 0.596 MiB/s | 0.512 MiB/s | -14.1% | 3/3 |
| io.sd-read (4096 B) | 1.888 MiB/s | 1.791 MiB/s | -5.2% | 3/3 |
| io.uart-tx (4096 B) | 0.011 MiB/s | 0.011 MiB/s | -0.0% | 3/3 |
| mem.chase32 (1048576 B) | 2130.086 ns/load | 2161.131 ns/load | +1.5% | 3/3 |
| mem.copy32 (1024 B) | 23.723 MiB/s | 24.842 MiB/s | +4.7% | 3/3 |
| mem.copy32 (4096 B) | 5.282 MiB/s | 5.269 MiB/s | -0.2% | 3/3 |
| mem.copy32 (65536 B) | 5.325 MiB/s | 5.302 MiB/s | -0.4% | 3/3 |
| mem.copy32 (1048576 B) | 5.329 MiB/s | 5.297 MiB/s | -0.6% | 3/3 |
| mem.libc-copy (1048576 B) | 5.165 MiB/s | 5.205 MiB/s | +0.8% | 3/3 |
| mem.libc-set (1048576 B) | 8.704 MiB/s | 8.633 MiB/s | -0.8% | 3/3 |
| mem.read32 (1024 B) | 18.338 MiB/s | 20.088 MiB/s | +9.5% | 3/3 |
| mem.read32 (4096 B) | 11.431 MiB/s | 12.231 MiB/s | +7.0% | 3/3 |
| mem.read32 (65536 B) | 8.312 MiB/s | 8.681 MiB/s | +4.4% | 3/3 |
| mem.read32 (1048576 B) | 8.318 MiB/s | 8.677 MiB/s | +4.3% | 3/3 |
| mem.write32 (1024 B) | 20.549 MiB/s | 23.417 MiB/s | +14.0% | 3/3 |
| mem.write32 (4096 B) | 19.432 MiB/s | 22.306 MiB/s | +14.8% | 3/3 |
| mem.write32 (65536 B) | 8.689 MiB/s | 8.736 MiB/s | +0.5% | 3/3 |
| mem.write32 (1048576 B) | 8.688 MiB/s | 8.744 MiB/s | +0.6% | 3/3 |

周期/延迟越低越快，MiB/s 越高越快。CPU 指标包含循环控制，不是 MIPS。缓存写入计时不能解释为物理 DDR 写回峰值。JSON 保留每组范围和样本数。

## 并发状态和边界

- LCD underflow baseline：`{'all': [0, 0], 'cache': [0, 27]}`（每个 UART 中的前后计数）。
- LCD underflow candidate：`{'all': [0, 0], 'cache': [0, 26]}`（每个 UART 中的前后计数）。
- Whole-system comparison includes C removal, XIP boot, RAM relocation, dynamic VRAM and place option changes; it cannot isolate XIP performance.
- Baseline uses preserved three-round measurements from the qualified v0.7.1 bundle; it was not reloaded in this session.
- Storage benchmark is read-only. Kernel boot, physical input and audible output are not qualified.
- 未匹配候选项目：`[]`；未匹配基线项目：`[]`。

## 原始数据

- baseline：`C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini\build\releases\v0.7.1\full-rv32imafc-rom4k-l24k\reports\performance\candidate\all\results.json`，SHA256 `fcfe2d1f164934e530e34e906cbbae792c1146edc7cbffe7808e756c305d1d95`，身份关联：recorded。
- baseline：`C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini\build\releases\v0.7.1\full-rv32imafc-rom4k-l24k\reports\performance\candidate\cache\results.json`，SHA256 `caa272dc2891361718416b64ae7e054bde68612034efe9ad8ad175bd7cb89e39`，身份关联：recorded。
- candidate：`C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini\build\worktrees\noc-flash-xip\build\reports\performance\merge-reviewed\all\results.json`，SHA256 `485ca0cb3eee0afbe24fa38afcd6516b24e8f2093705d8f9a1b43d499acedda5`，身份关联：recorded。
- candidate：`C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini\build\worktrees\noc-flash-xip\build\reports\performance\merge-reviewed\cache\results.json`，SHA256 `bc8ee16e510119e3d0f4aecb71faa947f5ee1a94446549a03c970af7ab1cc5ee`，身份关联：recorded。
