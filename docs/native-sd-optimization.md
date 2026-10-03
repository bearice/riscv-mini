# Native SD specialization and MMU/FPU hardware acceptance

2026-10-02. Work in progress: physical implementation and reduced-system
hardware tests pass; full-system hardware acceptance is still pending.

The default CPU remains lite. Explicit experimental build:

```powershell
.venv/Scripts/python.exe scripts/build.py --cpu-variant linux `
  --cpu-verilog build/cpu-qualification/reproduce-generator/VexRiscv_MmuFpu.v `
  --profile full --synthesize --output-dir build/native-optimized/board
```

## Changes

Native four-bit PHY, response/data CRC, timeouts, SCR initialization and both
512-byte FIFOs remain. The driver already limits transfers to eight sectors.
SDCore command CSR now uses its meaningful 14 bits, and block counters use
4 bits. The DMA engines accept aligned 8..4096-byte transfers to DDR with
32-bit base addresses, no loop mode or 64-bit descriptor. Each retains one
Wishbone request until acknowledgement/error, including software disable.
The read and write engines remain separate; the larger shared-DMA prototype
is isolated under `build/native-shared/source-snapshot`, not selected by source.

`sim/test_sd_dma_bounded.py` covers SCR/sector/eight-sector transfers, stalls,
invalid lengths and in-flight disable. `sim/test_sd.py` retains CRC/timeout and
descriptor ownership tests. Logs: `build/native-optimized/dma-simulation.log`
and `build/native-sd-simulation.log`.

## Matched physical results

| Configuration | Synthesis Logic / FF | PnR Logic / FF / CLS | BSRAM | Setup / hold violations |
|---|---:|---:|---:|---:|
| CSR-packed native before specialization | 19147 / 10802 | 577 unrouted nets, PR0004 | 43 | No valid timing result |
| Bounded native, full MMU/FPU peripherals | 18868 / 10741 | 19070 / 10773 / 10073 | 43 | 0 / 0 |

Savings are 279 synthesis Logic and 61 FF, not a large protocol redesign.
CLS is 10073/10368, leaving 295; BSRAM is 43/46 and PLL is 3/4. The first
qualification reports worst setup slack +0.367 ns. CPU/sys remains 60 MHz,
DDR CK 120 MHz and DLL off. The margin is small, so subsequent changes need
new PnR and hardware checks.

Evidence: `build/csr-packed/native-baseline/qualification.json`,
`build/native-optimized/qualification/qualification.json`, and
`build/native-optimized/board/validation.json` with its Gowin reports.
Qualification used historical ROM and is not a deployable firmware image.
The board build includes a new 6584-byte boot ROM and matching application.

## Hardware evidence and hierarchy failure

The minimal MMU/FPU build boots and passes `test fpu`, `test mmu`, UART,
interrupt, DDR scratch and read-only Flash/UID tests. FPU addition produces
0x40a00000 for 2+3, with exception flags zero. Sv32 translated load returns
0x53563332. Logs and structured results are under
`build/native-optimized/minimal-diagnostic/firmware-verification*`.

The same CPU with native SD and filesystem also passes single/multiblock
reads and `test sd write`, creating and rereading RVT00002.BIN. Final SD
status: four-bit, 7500000 Hz, reads 0xc0, writes 0x85, errors zero. Evidence:
`build/native-optimized/sd-diagnostic/firmware-verification*`.

The full bounded-native bitstream reports successful SRAM programming but
has not produced its boot banner, including after the user pressed FPGA
reset. Known accepted lite and the reduced MMU/FPU builds boot using the
same cable. SRAM Verify reports failure at address zero for both new and
known accepted images, so that operation is not a useful discriminator.
Initial UART logs contain startup of old Flash firmware and must not be
mistaken for acceptance of the new image. Evidence:
`build/native-optimized/board/probe-known-good.log`, `probe-mmu-fpu.log`,
`physical-reset.log`, `probe-minimal.log`, and SRAM verification logs.

CPU-only and actual Wishbone bus/ROM/SRAM simulation reach UART access;
temporary fixtures/results are under `build/native-optimized/boot_probe*`
and `bus_probe*`. Those models are not a board or timing simulation.

## Boundaries

FPU testing currently proves addition, not full IEEE-754/divide/sqrt coverage.
MMU testing proves one Sv32 supervisor data translation from M-mode using
MPRV, not user/kernel permissions or an OS. Interrupt handlers use integer
code; floating-point context save/restore for an RTOS is not implemented.
Full-system flat-RTL native SD + display/USB/audio five-minute acceptance has
passed. Ethernet controller/PHY checks pass, but the cable reported link down;
external packet roundtrip was not verified in this run. Flash configuration
has not been replaced by this candidate.

## Full-system hardware acceptance (flat RTL)

`build/native-optimized/full-flat-diagnostic/firmware-verification.json` records
PASS for all requested commands, including `test fpu`, `test mmu`, Flash UID,
native SD read/multiblock/new-file write and readback, USB receiver enumeration,
audio, microphone/stereo, displays and a 300-second concurrent soak. The user
confirmed both LCDs normal and stable during the soak. Final native SD reads
0x39c, writes 0x85, errors 0; RGB LCD underflows 0; audio underruns/errors 0;
USB connected 046d:c52b with three HID interfaces and errors/drop counters 0.
Audio remained muted. This is not a new physical keyboard/mouse acceptance.

The accepted candidate uses flat generated RTL. Final PnR: Logic 18540/20736,
FF 10774/16173, CLS 10055/10368, BSRAM 43/46, PLL 3/4, setup/hold violations
0/0. ABI 0xcc48ba91. Bitstream SHA256:
`fc6b28a4bf77170c3907541b9218c00fd432f9dd11b7904797c3d7db67880823`.
Application image SHA256:
`f643486ecb6a721d78306468eaac6baf2904546ab3cb0f30e7b3be7c73bbad6a`.
Evidence: candidate `validation.json`, `firmware-verification-uart.log`,
`gateware/impl/pnr/project.tr`, and `build/native-optimized-flat-verification.log`.

Flat USB constraints initially referenced hierarchical cells. The final
diagnostic corrected the PHY instance path and selected the actual registered
ULPI initialization outputs for the existing two-cycle setup exception.
`gateware/constraints.py` now emits these paths for flat mode and retains the
existing hierarchical paths. Regeneration in `build/native-optimized/flat-repro`
produces an SDC identical (after outer whitespace removal) to the accepted one.
No CPU, DDR data or USB core path was exempted to close timing.

Reproduce the full flat candidate with:

```powershell
.venv/Scripts/python.exe scripts/build.py --synthesize --flat-verilog --cpu-variant linux --cpu-verilog build/cpu-qualification/reproduce-generator/VexRiscv_MmuFpu.v --output-dir build/native-optimized/full-flat-rebuild
.venv/Scripts/python.exe scripts/firmware_verify.py --program --sd-write --mic --soak-seconds 300 --output-dir build/native-optimized/full-flat-rebuild
```

Default CPU remains lite and default generated RTL remains modular. This
candidate is a working MMU/FPU proof, not replacement of the accepted default
or completion of the modular MMU/FPU build. A fresh PnR is required for changed
sources/options; small margin does not establish frequency headroom.

## Rejected alternatives and open diagnosis

The original full hierarchical source closes PnR but does not boot on hardware,
including physical FPGA reset. The shared-SD-DMA prototype boots and runs
FPU/MMU/SD but reads wrong Flash JEDEC and fails `test flash`; it is isolated
under `build/native-shared/source-snapshot` and is not the source implementation.
Its PnR is 19117 Logic, 10524 FF, 10178 CLS, 43 BSRAM, zero setup/hold violations.
See `build/native-shared/board/firmware-verification.json` and UART log.

Keeping modular RTL files but setting Gowin `netlist_hierarchy=0` also completes
routing, with 19068 Logic, 10773 FF, 10120 CLS, 43 BSRAM; however it has 101
setup violations, worst -2.622 ns, hold 0. It was not programmed or accepted.
Evidence: `build/native-optimized/modular-flatnetlist.log` and that directory's
`gateware/impl/pnr/project.tr`. Removing Ethernet alone fails routing with 677
unrouted nets (`build/native-optimized-no-eth-diagnostic.log`). Neither result
proves the CPU is intrinsically too large or that Ethernet is the boot cause.

The difference between flat and hierarchical hardware remains unresolved.
CPU-only and actual-bus simulation reach UART, while the full SoC simulation
did not produce useful progress and is not hardware evidence. Do not claim
a hierarchy converter bug or Gowin bug has been isolated. No Flash programming,
power-cycle acceptance, user/kernel isolation or floating-point RTOS context
switching was tested by this experiment.
