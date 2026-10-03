# Native SD + MMU/FPU optimization plan

2026-10-02. Original planning record. Implementation and hardware results are
now recorded in [native-sd-optimization.md](native-sd-optimization.md).
Bounded native SD plus all peripherals and MMU/FPU passed five-minute hardware
verification with flat RTL. Hierarchical RTL is still not accepted; see that
record for its boot failure and flattened-netlist timing failure.
Goal: retain read/write native four-bit SD, all peripherals, MMU/FPU, sys60/DDR120;
close PnR timing with meaningful area/routing margin.

## Evidence and required baseline

CSR-packed native synthesis: Logic19147, FF10802, BSRAM43. SPI full MMU/FPU:
synthesis16380/9713/41, PnR16572Logic9371CLS41BSRAM, setup/hold0/0, reported
setup slack+0.944ns. Native SD module2381Logic plus CSR439=2820. However no
post-CSR-packing native PnR has been run: first establish that baseline before
attributing failures to SD or assigning a required saving.

NativeSD is flattened by current elaboration/reporting, so component costs are
unknown. Instrument an isolated synthesis with preserved submodule hierarchy
or equivalent net attribution to measure PHY/core/FIFO/DMA separately. Compare
whole-design totals to control instrumentation effects.

## Proposed implementation order

1. Low-risk bounded specialization: driver transfers at most8 blocks, also
   reads8-byte SCR in one-bit mode before switching four-bit. Keep block_length
   supporting8 and512; block_count=1..8 allows4-bit count including terminal8.
   DMA descriptors handle8 bytes and4096bytes, not512 only. Narrow internal
   counters/comparators with explicit command validation. Keep128-bit long
   responses, sector address32bits and complete DDR addresses. Preserve CRC,
   timeout/no-media/abort behavior. Measure one change at a time.
2. Consolidate mutually exclusive SD DMA read/write engines into one32-bit
   Wishbone master and descriptor (DDR address, length, direction). Keep only
   one request outstanding; do not cancel an accepted transaction on software
   abort. Drain completion before resetting buffer ownership. HAL/FatFs remain
   unchanged; sd_native.c adapts register programming. Native core and physical
   read/write protocol still distinct until separately proven shareable.
3. Replace duplicated byte-oriented DMA FIFOs/converters and excess staging
   with a shared sector buffer in BSRAM, preferably two512-byte banks for
   ping-pong operation. Pack incoming SD bytes into32-bit words; unpack writes
   from32-bit words. One sufficiently sized BSRAM can hold both banks subject
   to Gowin mapping verification. Prefill write sector, receive full read
   sector, manage bank ownership/CRC validity; preserve small SCR transfer.
   Backpressure cannot be assumed to propagate to a card sending data; prove
   overrun/underrun behavior under DDR contention. Do not blindly reduce FIFO.
4. Share timeout/control resources or unify1-bit/4-bit deserialization only
   after area attribution. Initialization needs1-bit SCR; cannot simply remove
   one-bit path. Read/write CRC16 sharing requires bit-accurate schedule tests;
   retain checks and result propagation. Core states/R1b/write busy handling
   remain correctly distinct even if counters are reused.

## Gates

After each step compare full-system synthesis and run command/response, SCR,
1/4-bit data, CRC failure, timeout, DMA backpressure/abort simulations. Attempt
PnR once useful savings appear. A first working target is several hundred
Logic saved; ~800-1200 total savings is an engineering exploration target, not
an estimate guaranteed by current report. Success criterion is setup/hold0/0
at60MHz, not hitting an area number. If low-risk specialization is sufficient,
stop before DMA rewrite. If not, sector-buffer/DMA consolidation is preferred
larger redesign, retaining throughput rather than byte PIO.

On hardware use SD read/write file verification and max five-minute SD/display/
USB/network/audio coexistence. User approval of pin-dependent actions and
existing skip/no-unplug preferences persist. MMU/FPU software/cache acceptance
is separate; physical implementation success alone does not prove execution.

Evidence: gateware/sd.py, gateware/vendor/sdcore.py,
firmware/drivers/sd_native.c, installed litesdcard/frontend/dma.py and phy.py,
build/csr-packed/mmu-fpu/qualification.json,
build/csr-packed/mmu-fpu-spi/qualification.json,
docs/spi-mmu-fpu.md and docs/synthesis-module-resources.md.
