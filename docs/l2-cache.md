# Optional shared DDR read cache

Build with `--l2-size 4096` (or `8192`); default `0` keeps the original converter.
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

## Tests and candidates

`tests/l2_test.py` uses delayed fake memory to test lane reuse, conflict,
partial writes, bypass, errors, abort and reset with persistent RAM.
Firmware `test l2` checks repeated reads and byte writes, printing bypass/cache
ticks with L1 D-cache flushed between passes. It masks interrupts briefly
and uses 256 bytes at `0x40d00000`. SD, framebuffer CRC, audio and concurrent
tests exercise the shared path on a timing-clean board candidate.

```powershell
.venv/Scripts/python.exe scripts/build.py --l2-size 4096 --synthesize --output-dir build/l2/no-c
.venv/Scripts/python.exe scripts/build.py --cpu-verilog build/ddr-hw/cpu-pipeline/VexRiscv_MmuFpuC.v --l2-size 4096 --synthesize --output-dir build/l2/c-only
```

Both retain full MMU/FPU, SD lite, USB ultra, both displays, Ethernet, audio and
dual microphones at sys60/DDR120. C-only uses two-cycle I-cache and injector,
without B. Simulation does not establish RAM allocation, timing or performance.
PnR and board results are appended after qualification.

## 2026-10-03 results

| Full peripheral candidate | Boot bytes | Logic | FF | CLS | BSRAM | Setup/hold | Board |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| No C/B + L2 4KiB | 4024 | 18820 | 10771 | 10118 | 43 | 0/0 | Passed |
| C-only + L2 4KiB | 2888 | 19133 | 10831 | 10143 | 43 | 0/0 | ROM DDR error |

Capacities: Logic20736, FF16173, CLS10368, BSRAM46; both use PLL3/4,
IO139/207 and PRIMARY/LW8/8. Final no-C report's first setup path slack is
+0.175ns on a USB ULPI output; positive but small, not a frequency-headroom claim.
Final candidate: `build/l2/no-c-release`, ABI `552653d0`, CPU RV32IMAF
with MMU/FPU and 2KiB I/D cache, sys60/DDR120, SDlite and all peripherals.
Defaults are not switched to an experimental CPU or L2: `--l2-size` stays 0
unless selected. The board is running this no-C/L2 candidate in FPGA SRAM with
UART-loaded app; persistent Flash remains the previous production version.

The final linked loader has text3556 + rodata468, data0 and bss4; ROM uses
4024/4096 bytes. L2 consists of four SP data primitives and one SP tag primitive
in `gateware/impl/gwsynthesis/project.vg`. Historical 43 BSRAM becomes38 with
ROM shrink/SRAM removal/hardware init, then43 after adding L2. L2's five blocks
reuse the net saving; three blocks remain free. No 8KiB L2 was built.

Final evidence under `build/l2/no-c-release/`:

- `validation.json`: full PnR/resources/artifact hashes.
- `firmware-verification.json`: 38 command kinds, including MMU, FPU, ISA, L2,
  native SD read/write, displays, USB restarts, audio, Ethernet HAL and dual mics.
- `boot-verification.json`: UART boot, invalid headers/ABI/addresses/CRC and
  truncated input, then full software reset and UART boot again; no Flash write.
- `external-verification.json`: 300.313s, 110 rounds, 1100 UDP packets,
  379830 payload bytes, zero timeouts/mismatch, max RTT16ms, concurrent SD,
  USB/audio/display checks. Line-in L750/R500Hz, separation32.91/32.79dB,
  mute attenuation64.23/56.59dB, zero clipping. User confirmed both LCDs stable.
- `reset-verification.json`: five full software resets into loader, final UART
  load, and DDR/L2/LCD/SPI-LCD/USB checks. Final training lane0 `0099024c`
  (window153, slip2, tap76), lane1 `009b024d` (window155, slip2, tap77).
- `reset-verification-uart.log`: three `test l2` repeats: bypass31129/31633/32102
  ticks, cached10145/10157/10162 ticks at60MHz. Median527.22us versus169.28us,
  3.11x. Earlier single-command samples ranged2.02–2.85x; this is a short,
  cache-state-dependent 256-byte read test, not an overall application speedup.
- `boot-size.json`: per-function loader size and section/alignment accounting.

Two C-only bitstreams (`build/l2/c-only`, `build/l2/release`) passed PnR but
reported exactly `ERR DDR INIT/TIMEOUT; reset` before entering loader main.
No L2 data request or DDR stack is needed during that ROM poll. The error text
does not distinguish the hardware failure bit from software timeout; cause
remains unknown, and these builds are not functionally accepted. Changing CPU
also changes placement, so these observations do not establish a C ISA defect.
No fresh keyboard/mouse input, cable/card removal, Flash install or physical
powercycle was performed in this trial. Five resets do not prove cold powercycle.
