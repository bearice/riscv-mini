# Shared DDR read cache (default on)

The default build includes `--l2-size 4096`; `--l2-size 0` restores the original
32→128 Wishbone converter, and `8192` is offered but has never been built.
CPU ISA selection uses `--cpu-verilog` and matching generator metadata.

The default `--l2-backend native` incorporates the native command/write/read handshake into
the write-through L2 FSM, removing its 128-bit Wishbone interface and bridge.
It retains cache capacity and byte-write semantics; the memory scheduler is
selected independently. It requires an enabled write-through L2; `writeback` and
`--l2-size 0` are incompatible. Native has no bus-error response channel.
Controller simulations and synthesis do not qualify board operation or speed.
Board qualification applies to a concrete
bitstream and its matched firmware, rather than to the backend flag alone.

`--memory-scheduler crossbar` is the default selection. CPU/L2 and
LCD each receive a native crossbar port; the custom shared scheduler and its
spacing CSR are removed. Commands remain blocked until DDR hardware boot is
ready. LiteDRAM arbitrates banks, without the custom LCD-priority policy.
Native DMA, if enabled, uses an additional crossbar port and remains subject
to its own qualification boundary; `--dma-shared` is incompatible. The
fallback is `--memory-scheduler shared`.

`--l2-fast` is enabled by default for native `burst-refill` L2. It
launches requests one cycle earlier while keeping replies registered, prefetches
the tag/data lookup, and invalidates store tags without waiting for a tag read.
The request launch adds a combinational path through the bus fabric; each full
configuration therefore requires its own timing and board qualification.
Use `--no-l2-fast` to disable it. Selecting another L2 mode or Wishbone backend
disables it unless explicitly requested. `--l2-size 0` selects the Wishbone
converter automatically; `--l2-backend wishbone` remains available as a fallback.

`--l2-combine` combines writes only within an incrementing Wishbone burst
(`CTI=2`). A different 16-byte line or a read drains the partial word; the final
beat (`CTI=7`) waits for native submission before ACK. Cancellation drains the
accepted prefix. Classic stores keep their completion semantics and are not
posted across independent transactions: the default CPU does not export its
internal `fence` instructions. This is not a dirty writeback cache and needs
no software flush CSR.

With `--l2-combine`, the Wishbone SD `lite` receive DMA issues bounded four-word
bursts. The final beat completes before DMA done. A stalled input releases the
bus by draining the partial burst after 256 waiting cycles; disable also drains
it through a zero-byte-enable terminal beat. SD `full`, SPI and native-DMA
backends retain their existing transfer paths. Both options default off.
The combined fast/SD-burst implementation remains unqualified: a timing-clean
full candidate failed hardware DDR initialization on repeated SRAM downloads.
Simulation verifies write merging and completion ordering, but does not establish
physical DDR startup or a board throughput improvement for this combination.

`--l2-posted` is a separate opt-in experiment for independent classic stores.
It requires a CPU generated with `scripts/cpu_generate.py --external-fence`,
native `burst-refill` L2, Wishbone DMA, and SD `none`, `spi` or `lite`.
The default CPU exports fence signals, but posted writes remain disabled. This is a bounded write
buffer, not a dirty-line writeback cache: stores invalidate L2 and merge byte
masks in one 16-byte word; address changes, reads and barriers drain it.
An idle timeout of 32 sys cycles also drains a lone accepted write. The
framebuffer/bypass region remains non-posted. SD-lite uses bounded bursts with
non-posted terminal beats so its completion still implies memory submission.

The CPU exports `externalFenceRequest`, `externalFenceDone` and `externalAtomic`.
FENCE and FENCE.I wait in decode for older pipeline instructions and cached-core
command pipes, then hold until the L2/native path is idle. The instruction-cache
invalidation is delayed until that completion. All fence masks use a full
drain. AMO/LR/SC also drain before execution; atomic state remains asserted until
the queued command has completed, keeping its store non-posted. A CPU data-bus
bridge holds non-DDR accesses until pending DDR writes have drained, including
DMA doorbells. This does not provide snooping of CPU L1 or make native DMA
coherent; the existing software cache-maintenance rules still apply.

The fence-export CPU has passed RTL simulation and a full-system board test
with the native fast L2 path and posted writes disabled. Posted writes remain
unqualified: timing-clean candidates have failed physical DDR initialization
or the bootloader's payload CRC check. Keep `--l2-posted` disabled for normal
use; neither its simulations nor a successful fence-only test qualify the
buffered-store configuration.

`tests/l2_combine_test.py --fast --posted` verifies independent-store merging,
fence visibility, ordered reads, atomic store completion and bounded idle drain.
`tests/cpu_order_test.py` verifies MMIO gating. `scripts/test_cpu_fence.py`
executes delayed-fence, self-modifying code, FENCE.I, AMO and LR/SC sequences on the generated CPU RTL using
Icarus Verilog. Firmware `test fence` checks store/byte-write visibility, AMO
where supported, and execution of modified code at the same address.

## Path and consistency

`gateware/l2.py` replaces the 32→128 Wishbone converter between the registered
main-RAM bus and LiteDRAMWishbone2Native. Its 16-byte direct-mapped lines cache
reads. Writes go to DDR with original byte enables and invalidate the indexed
line. Write ACK waits for the DDR bridge. No dirty lines or software L2 clean
operation are required. CPU L1 rules still apply, including SD DMA invalidation.

CPU, native SD reader/writer and audio DMA share this Wishbone path. RGB LCD
uses the native read-only port. Addresses at/above `0x47e00000` bypass L2.
The optional native DMA backend exposes tag invalidation and busy CSRs; the HAL
invalidates L2 and CPU D-cache after DMA writes complete. See [native DMA](native-dma.md)
for its experimental qualification boundary. This is not automatic DMA coherence.

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
is `burst-refill`, with native fast requests and registered replies. `baseline` retains
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

With the shared scheduler, non-baseline modes expose `memory_port_spacing` to control extra native transaction spacing, resetting to
zero sys cycles. BIOS `bench gap 0/2/4/8` changes it without saving settings.
Zero removes the additional quiet interval; LiteDRAM command/data timing rules
remain in effect. This CSR changes the generated register map, so an older
firmware image must not be paired with the new FPGA configuration.
The baseline mode keeps the original fixed interval and has no spacing CSR,
preserving its legacy register map and image ABI. The current full default
uses image ABI `43623d5e`; build firmware and FPGA configuration together.

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
