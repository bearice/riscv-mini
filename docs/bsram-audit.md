# BSRAM audit and DDR boot feasibility (2026-10-03)

Historical baseline: Flash build `build/soc-layout/release/`, ABI `997bd228`.
The newer hardware-DDR/ROM4KiB/SRAM0 experiment and optional L2 are documented
in [hardware-ddr-boot.md](hardware-ddr-boot.md) and [l2-cache.md](l2-cache.md).
The census below describes the historical baseline, not current working RTL.
Hierarchy rows report own resources; CPU totals below include descendants.
The hierarchy census and synthesized primitive census independently sum to 43,
matching PnR 43 / 46. Machine-readable evidence: `bsram-audit.json` in that build.

| Owner | BSRAM | Logical use |
| --- | ---: | --- |
| Ethernet | 14 | Two RX slots (2), two byte-write TX slots (8), two 42-bit CDC FIFOs (4) |
| CPU | 12 | Integer RF (2), I-cache (2), D-cache (5), FPU RF (3) |
| Boot ROM | 4 | Configured 8 KiB; current binary 6584 bytes |
| Integrated SRAM | 4 | Configured 8 KiB, 32-bit Wishbone with byte writes |
| RGB LCD | 4 | DDR-reader FIFO: 512 x 130 bits, 8 KiB pixel data plus flags |
| Native SD lite | 2 | Read/write DMA byte FIFOs, each 512 x 8 bits |
| Dual microphones | 2 | Snapshot FIFO 512 x 48 bits |
| Audio output | 1 | FIFO 511 x 32 bits plus buffered output |
| Total | 43 | DDR framebuffers and application memory are external DDR |

Ethernet details are confirmed in generated `riscv_mini__ethmac.v` and synthesis
`project.vg`: RX memories are 383 x 32 bits each, TX memories are logically the
same size but each splits into four byte banks, and CDC FIFOs are 32 x 42 bits.
The HAL already fills TX slots with aligned 32-bit writes. CPU generated RTL has
a 32 x 32 integer RF replicated for two read ports; 32 x 34 FPU RF replicated for
three read ports; 2 KiB I/D data caches with tags. D-cache data splits into four
byte memories. BSRAM usage therefore reflects width, ports and write granularity,
not just useful byte capacity. USB PIO, UART, SPI, CSR, timers and DDR controller
allocate zero BSRAM in this build; this does not imply zero registers or SSRAM.

## Integrated SRAM purpose

`integrated_sram_size=8*1024` in `gateware/soc.py` adds a writable CPU memory at
0x10000000..0x10001fff. `firmware/bootloader/boot.ld` puts data/BSS and the boot
stack there, with SP initially 0x10002000 and at least 2 KiB stack reserved.
`firmware/boot/start.S` sets this stack before calling C or training DDR.
Boot code executes from the separate ROM at address zero. Flash/UART payloads
are loaded directly into DDR at 0x40800000, not staged in integrated SRAM.

Current boot map: data=0, BSS=4 bytes (`capacity`), leaving 8188 bytes for stack
and padding. UART packet buffer is 134 bytes, image headers 48 bytes; both are
automatic objects. App linker puts all sections and stack in DDR; app SP is
0x40c00000, with 64 KiB reserved, so the monitor does not use the boot SRAM stack.

Compiler `-fstack-usage` audit with the existing LTO flags reports a maximum
individual frame of 288 bytes (`main`). This is not the maximum nested stack
depth and is not an on-board stack-watermark measurement. Evidence:
`build/soc-layout/boot-stack.log` and `boot-stack/boot.elf.ltrans0.ltrans.su`.

SRAM currently maps to four SP primitives, each BIT_WIDTH=8, independently
controlled by a Wishbone byte enable. Simply reducing capacity may retain four
blocks; no smaller-SRAM synthesis was performed. A single word-wide memory
would require preserving partial stores through suitable byte enables or a
serialized read/modify/write path. Removing SRAM after independent hardware DDR
bring-up has a nominal target saving of all four blocks, pending implementation.

## Hardware DDR initialization/training proposal

Feasible, not implemented. The CPU already has a DDR Wishbone path while executing
ROM; the dependency is DDR readiness before C uses its stack/data.

Current implementation has three phases:

1. `GW2DDRPHYInit` already initializes the FPGA DLL/DQS/clock gate/reset in hardware.
2. Generated JEDEC `init_sequence()` is executed by C: RESET/CKE, MR2/3/1/0 and ZQ.
3. `firmware/boot/ddr.c` performs two-lane training: four bitslips, 256 taps,
   three seeds (42/84/36), burst detection, lane-byte comparison, widest valid
   window selection and midpoint verification. It then hands DFI ownership to
   LiteDRAM and runs the normal-controller memory test.

Hardware migration must cover JEDEC and read training, not merely the PHY's
existing DLL sequence. A proposed standalone sequencer owns DFI and PHY training
controls, emits patterns from constants/registers without a RAM buffer, selects
the measured windows, then verifies the controller data path before asserting
ready. Expose ready/failed plus per-lane window/tap/bitslip diagnostics. Wait for
stable PHY initialization; command spacing must be derived from actual DDR
timing, not translated from C delay-loop iteration counts one-for-one.

ROM startup can use a stack-free assembly loop to wait on ready/failed, then set
SP to a reserved DDR boot-stack region, initialize data/BSS and enter the existing
Flash/UART loader. Until ready it must not call C or access DDR-backed variables;
a failure path must also avoid a DDR stack. Training/test scratch, boot stack,
application and framebuffer ranges must be disjoint when simultaneously live.
Software resets need defined re-training and DMA-quiesce ownership semantics.

Removing integrated SRAM can target four BSRAM saved; shrinking boot ROM is an
additional possible benefit only if its final linked size crosses a physical
block boundary. FSM area/timing and ROM savings are not measured. Current CLS
10081 / 10368 is tight, so synthesis/PnR and board validation are necessary.
This audit changed no RTL behavior, firmware, board state or Flash contents.
