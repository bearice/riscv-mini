# Peripheral CSR packing

The peripheral CSR interface is distinct from CPU instruction CSRs such as
mstatus and satp. Narrow, read-only peripheral values now share bus words.
Controls, commands, DMA addresses and full-width counters retain their existing
semantics. All field widths remain unchanged, including 16-bit microphone counts.

`gateware/csr_layout.py` is the shared layout definition. Gateware packs live
state Signals into CSRStatus words; `scripts/build.py` emits the original C
read accessor names as field extraction functions. Existing HAL and application
sources therefore retain their interfaces. Optional features emit aliases only
when the corresponding packed word exists.

| Bank / word | Fields (bit positions) | Previous words | New words |
|---|---|---:|---:|
| board_io / inputs | buttons 3:0; switches 7:4; pressed 11:8; released 15:12 | 4 | 1 |
| mic / counts | level 15:0; captured 31:16 | 2 | 1 |
| mic / state | busy 0; done 1; activity 2; activity_right 3 | 4 | 1 |
| audio / state | level 15:0; errors 18:16; busy 19; amplifier 20 | 4 | 1 |
| rgb_lcd / state | active 0; busy 1 | 2 | 1 |
| Total | | 16 | 5 |

A single packed word read captures its fields together. Separate legacy read
accessor calls still perform separate bus reads and do not promise a common
snapshot. Sticky button events and their independent write-to-clear command,
FIFO pop/capture commands, and all clock-domain crossings are preserved.

## Verified results (2026-10-02)

Same CPU RTL SHA256:
`45333b754ab68726f6eb23ede586af40cb5f2d7990569a60fcc8b6f9cd45fc97`.
Full feature set, lightweight PIO USB, sys60 MHz / DDR120 MHz.

| Synthesis resource | Before | Packed | Change |
|---|---:|---:|---:|
| Logic | 19248 | 19147 | -101 (-0.525%) |
| Register | 10791 | 10802 | +11 |
| BSRAM | 43 | 43 | 0 |
| CSR bank array subtree LUT | 1652 | 1635 | -17 |
| CSR bank array subtree Register | 1488 | 1498 | +10 |

Gowin hierarchy rows report each module's own resources. Subtree totals sum
rows once. Moving internal status Signals from CSR objects into the peripheral
also changes resource attribution across hierarchy boundaries; whole-design
numbers are the appropriate measure of actual savings. Packing preserves state
bits, and different readback mux/register inference can increase FF usage.
This is a modest area improvement, not a solution to full CPU timing closure.

Evidence:
- Before: `build/usb-light/mmu-fpu-final/qualification.json` and its
  `gateware/impl/gwsynthesis/project_syn_resource.html`.
- After: `build/csr-packed/mmu-fpu/qualification.json`,
  `build/csr-packed/mmu-fpu/synthesis-only.log` and the same hierarchy report.
- Comparison: `build/csr-packed/comparison.json`.
- Full firmware/generation: `build/csr-packed/full/validation.json`;
  boot 6568 bytes, app binary 68368 bytes, image ABI `0x850888ef`.
- Minimal firmware/generation: `build/csr-packed/minimal/validation.json`;
  boot 6560 bytes, app binary 8920 bytes, image ABI `0xf64f7df1`.
- Simulation: `tests/csr_packing_test.py`, output in
  `build/csr-packed/simulation.log`: bus readback and isolated field offsets,
  read-only write immunity, button debounce and sticky event clear.

Reproduce simulation with `.venv/Scripts/python.exe tests/csr_packing_test.py`.
Build firmware with `scripts/build.py --output-dir build/csr-packed/full`, or
add `--profile minimal` for the minimal profile. For the MMU/FPU synthesis use
`scripts/cpu_qualify.py --cpu-verilog build/cpu-qualification/reproduce-generator/VexRiscv_MmuFpu.v --output-dir build/csr-packed/mmu-fpu --synthesis-only`.

## ABI and acceptance boundary

The physical CSR address map changed. The build generates a different boot
image ABI tag from the new map; FPGA configuration, ROM and DDR application
must be rebuilt and used together. Never pair a new application with the old
board image. No board download or Flash write was performed for this change.
The packed design has synthesis and software compilation evidence only; PnR,
60 MHz timing, and real hardware peripheral regression were not run. Earlier
accepted lite board behavior remains the hardware baseline. MMU/FPU software
and hardware execution remain unqualified.
