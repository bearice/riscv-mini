# Shared DDR read cache (default on)

The default build includes `--l2-size 4096`; `--l2-size 0` restores the original
32→128 Wishbone converter, and `8192` is offered but has never been built.
CPU ISA selection uses `--cpu-verilog` and matching generator metadata.

## Path and consistency

`gateware/l2.py` replaces the 32→128 Wishbone converter between the registered
main-RAM bus and LiteDRAMWishbone2Native. Its 16-byte direct-mapped lines cache
reads. Writes go to DDR with original byte enables and invalidate the indexed
line. Write ACK waits for the DDR bridge. No dirty lines or software L2 clean
operation are required. CPU L1 rules still apply, including SD DMA invalidation.

CPU, native SD reader/writer and audio DMA share this Wishbone path. RGB LCD
uses the native read-only port. Addresses at/above `0x47e00000` bypass L2.
A future native DMA writer would need L2 invalidation or an uncached region;
this design does not promise arbitrary DMA coherence.

Data RAM has full 128-bit read-fill writes, avoiding byte-bank fragmentation.
Tag RAM valid bits are cleared by a reset sweep. Uncached reads invalidate
the reused RAM slot, so no old tag can point to overwritten data. There is no
combinational refill bypass. The upstream pipeline, native bridge and eight
sys-cycle CPU/video transaction spacing remain.

`l2_enable` controls read caching (reset 1); writes always pass through.
`l2_stats` packs wrapping 16-bit hit/miss counters in low/high halves.

## 操作和验证入口

默认 `--l2-size 4096`，`0` 关闭，`8192` 是未完成布局布线/上板验收的可选容量。默认 CPU 无 C/B 扩展。L2 的五个 BSRAM 与系统分配见 [BSRAM](bsram-audit.md)。

`tests/l2_test.py` 使用延迟内存检查 lane、冲突、部分写、bypass、错误、abort 和复位。固件 `test l2` 对 `0x40d00000` 的 256 B 检查重复读取及 byte write；测量期间短时关中断并在每次读取前刷新 L1 D-cache。输出的 bypass/cache ticks 是该小测试的耗时，不能解释为整体应用加速比。
