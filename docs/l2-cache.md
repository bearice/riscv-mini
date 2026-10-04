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
the reused RAM slot, so no old tag can point to overwritten data. The default
`burst-refill` path reuses adjacent lanes and returns DDR data while filling RAM;
the upstream pipeline registers requests and replies. Extra native transaction
spacing resets to zero; LiteDRAM timing constraints remain in force.

`l2_enable` controls read caching (reset 1); writes always pass through.
`l2_stats` packs wrapping 16-bit hit/miss counters in low/high halves.

## Build paths

`--l2-mode` selects the controller independently of its capacity. The default
is `burst-refill`, with a registered, write-through path. `baseline` retains
the original lookup/refill implementation and fixed eight-cycle spacing.

| Mode | Behavior |
| --- | --- |
| `baseline` | Registered request, synchronous tag/data lookup and RAM refill reply |
| `burst` | Keeps a filled 128-bit word available for adjacent 32-bit read beats; the Wishbone pipeline still registers every request/reply |
| `burst-refill` | Also replies with the requested DDR word while filling RAM; the reply is captured by the upstream pipeline, and subsequent lanes wait for RAM visibility |
| `prefetch` | Presents the incoming index to synchronous RAM during request capture, saving one lookup cycle |
| `writeback` | Experimental LiteX Cache wrapper with full-word byte merging, dirty eviction and explicit clean |

The writeback path reserves `0x47d00000` through `0x47d00000 + L2_size - 1`
as an eviction-read window. It provides `l2_flush` and `l2_busy`; software must
clean dirty lines before a native reader needs DDR visibility. CPU `fence`
alone does not clean L2. Disabling this cache cleans it first, reset clears its
tags, and framebuffer accesses still bypass it. Cached request cancellation
and error forwarding remain unsupported, so this path is not a production
backend. Its store benchmark excludes explicit clean time; libc benchmarks
include the finishing clean.

Non-baseline modes expose `memory_port_spacing` to control extra native transaction spacing, resetting to
zero sys cycles. BIOS `bench gap 0/2/4/8` changes it without saving settings.
Zero removes the additional quiet interval; LiteDRAM command/data timing rules
remain in effect. This CSR changes the generated register map, so an older
firmware image must not be paired with the new FPGA configuration.
The baseline mode keeps the original fixed interval and has no spacing CSR,
preserving its legacy register map and image ABI. The current full default
uses image ABI `1a87971c`; build firmware and FPGA configuration together.

Controller simulation alone cannot qualify these modes. Each concrete full
build needs zero setup/hold violations and board tests, including external
Ethernet payload checks. A timing-clean image can still fail a board data path.

`tests/l2_burst_test.py` covers lane changes, line crossings and registered
pipeline bubbles; `--refill-bypass` exercises early replies. The same option in
`tests/l2_test.py` retains byte-write, error, abort and reset checks.
`tests/l2_writeback_test.py` covers dirty eviction, byte merging, bypass, clean
and reset. `tests/l2_performance.py` compares fixed-latency controller models;
its cycles exclude CPU arbitration, physical DDR and video contention.

## 操作和验证入口

默认 `--l2-size 4096`，`0` 关闭，`8192` 是未完成布局布线/上板验收的可选容量。默认 CPU 无 C/B 扩展。L2 的五个 BSRAM 与系统分配见 [BSRAM](bsram-audit.md)。

`tests/l2_test.py` 使用延迟内存检查 lane、冲突、部分写、bypass、错误、abort 和复位。固件 `test l2` 对 `0x40d00000` 的 256 B 检查重复读取及 byte write；测量期间短时关中断并在每次读取前刷新 L1 D-cache。输出的 bypass/cache ticks 是该小测试的耗时，不能解释为整体应用加速比。
