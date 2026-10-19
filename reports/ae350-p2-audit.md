# AE350 P2 implementation audit — 2026-10-18

P2 remains incomplete. The existing work provides useful DDR-IP calibration and
vendor-reference evidence, but does not establish an 8 KiB architectural data
limit, a 128 MiB DDR capacity limit, or an unavoidable sub-100 MHz bus limit.
Several concrete adapter and diagnostic defects invalidate those attributions.
This audit does not replace the implementation agent's original report or modify
its RTL. No Flash was written and no acceptance target was reduced.

## Capacity and diagnostic interpretation

Gowin MUG1031-1.3.1E, Table 3-2, specifies the data-memory interface at
0x00000000–0x7fffffff, maximum 2 GB. Table 3-1 separately describes 64 KiB DLM at
0xa0200000. The project's 8 KiB boot SRAM is a fabric allocation, not evidence
of an 8 KiB hard-core memory limit. The 128 MB Extended AHB Slave aperture in
Table 3-3 is a different interface and does not cap the DDR data-memory port.

In `mega138k_ae350_p2.py`, the ROM diagnostic register is connected to
`self.ddr_ext.debug`, whereas accesses around 0x2000 are intended for
`self.ddr_port`. Observing no requests in the former does not establish that the
hard macro rejected accesses on the latter. `08000304` is an abstract-command
error status, not a returned DDR data word or an explanation of the physical
source of the access error. Its cause still needs direct port observation and
a CPU-executed load/store probe.

The nominal 1 GiB board capacity remains a geometry expectation, not a completed
capacity test. The vendor `pro_ddr_test` source writes the same 0xAA pattern at
every tested address and reads it back. `num_ok=0xffffff` corresponds to
16,777,215 successful 32-byte comparisons: 512 MiB minus 32 bytes. This is useful
functional evidence at 400 MHz CK, but cannot detect address aliasing between
identically filled locations, establish 1 GiB capacity, or qualify CPU access.

## Defects in the current adapter

These findings are from the current uncommitted source, rather than an assertion
that every historical bitstream contains exactly the same defects.

1. `DDR3AHB.__init__` overwrites the integer capacity argument `size` with
   `Signal(3)` before constructing `bus.addr < base + size`. The range limit is
   consequently based on a latched transfer-size field, initially zero, instead
   of 1 GiB or 128 MiB. An isolated AHB simulation, replacing asynchronous FIFOs
   with interface-only stubs, issues a word request at base 0x2000 and observes
   `n_req=1, last_bad=1, last_in_range=0`, followed by ERROR. This reproduces a
   fabric rejection without any AE350 macro involved. It is not a DDR/CDC test.
2. `rw` is loaded directly from AHB `HWRITE`, but `ui.cmd=Mux(rw,1,0)` and the
   read/wait-data branch treat one as read. The vendor reference uses command
   zero for write and one for read. AHB reads become DDR writes and vice versa.
3. Responses are packed as `Cat(error, rdata)` but decoded as data bits 0:64
   and error bit 64. Successful data is shifted and the true error is decoded
   as a data bit. The response layout must be consistent in both domains.
4. `ui.addr=uiaddr[2:31]` never subtracts `base`, despite the documented address
   contract. The external-window base therefore contaminates the physical
   DDR address. The low SRAM carve-out also lacks its declared translation.
5. Both `ddr_port` and `ddr_ext` independently drive the same `self.ddr.ui`
   outputs without arbitration. The two low-memory slaves also both receive
   active `HTRANS` and `sel=1`; the response mux alone does not prevent the
   unselected slave from accepting a transaction.
6. `ERROR2` has no transition back to `ADDRESS`; after one rejected request the
   adapter cannot recover. The nominal response timeout only counts in WAIT,
   so a FIFO stall in DATA is not bounded by that counter.

The range predicate and variable alignment expression also add unnecessary
combinational logic to address paths. Correctness must precede frequency
conclusions, even if the current bitstream reports successful DDR calibration.

## Timing and the vendor integration reference

The actual timing report under
`build/work/ae350-rtl/6657305e68431778a9da9a6547a72d0c8b0287795901012cc6340abe84561239/gateware/impl/pnr/project_tr_content.html`
contains paths ending in `ddr_ext_last_bad_s0/D` and `fsm0_state_2_s1/D`.
These are fabric diagnostic/FSM registers from this project's adapter. Their
source being an AE350 output does not make the complete path internal to the
encrypted macro. The report's assertion that no fabric change can pipeline
these paths is unsupported. Failure of this implementation at 100 MHz is real;
an architectural 100 MHz impossibility is not established.

The local `ae350_customized_demo` explicitly connects the CPU's 64-bit
`DDR_H*` interface to vendor `gw_ahb_ddr3_top`, using exported `DDR_HCLK` and
`DDR_HRSTN`. Its generated PLL configuration gives:

| Clock | Configuration |
| --- | --- |
| CPU | 800 MHz |
| AHB / APB | 100 MHz / 100 MHz |
| AE350 data-memory clock | 50 MHz |
| DDR reference | 50 MHz |
| DDR CK | 400 MHz |

The top-level Verilog comment says DDR CK 200 MHz, but the DDR PLL `.ipc` says
`Clkout2ExpectedFrequency=400` and the generated PLL sets `ODIV2_SEL=2`.
Use the generated parameters rather than the stale comment. This reference
does not establish that our replacement bridge will close with its data-memory
clock increased to 100 MHz, but it supplies a concrete CPU-to-DDR topology;
there is no need to infer that the external peripheral window is the only route.

MUG1031 Table 4-1 allows core up to 800 MHz and data/AHB/APB clocks below
200 MHz; AHB and APB must be synchronous 1:1. Table 4-2 allows DDR CK up to
400 MHz. These are supported limits, not guarantees for arbitrary fabric logic.
The reference projects are not evidence for every P2 acceptance condition:
full-capacity alias tests, cache maintenance, CPU DDR execution, repeated reset,
and sustained stress still require explicit results.

## UART and P1 recheck

The original P1 receipt
`build/runs/20261018T013840070148Z-board-ae350-run/result.json` contains a
matching ROM banner, advancing status counters, successful UART echo, ebreak
trap text, and recovery. Describing P1 as historically UART-silent is incorrect.

This audit reprogrammed the exact P1 artifact with the supported command:

```text
mini.py board run --machine mega138k_ae350 --build build/runs/20261018T013748121731Z-build-p1-commit-final-e1694d/result.json --port COM9 --location 6705 --cpu-jtag-location 107569
```

The fresh receipt is
`build/runs/20261018T035543637185Z-board-ae350-run/result.json`.
SRAM programming and the 1056-byte JTAG ROM comparison pass, but UART times out.
A subsequent JTAG halt observes PC 0x80000300 and zero mcause/mepc/mtval; the CPU
was resumed afterwards. COM9 still identifies as onboard USB serial
2025030317B. UART silence is therefore currently reproducible even on the old
P1 bitstream; its cause is unresolved and cannot be attributed exclusively to
the P2 wrapper. The board is left running that exact P1 SRAM image.

The new P2 `boot_capture` also omits the JTAG-assisted restart used in the P1
workflow. This is a workflow regression to correct, although it does not by
itself explain the fresh P1 UART failure.

## Acceptance implications and next work

Retain P2 as incomplete. Keep the nominal capacity and 400 MHz CK target;
neither reduction has an evidential basis as a hardware restriction. Lower
frequencies may be diagnostic configurations, with the data-memory clock,
AHB/APB clock, and physical DDR CK recorded separately.

First repair and simulate the adapter's range checking, read/write command,
response packing, address translation, slave selection/arbitration, error
recovery and backpressure. Then observe the correct low data-memory port while
executing CPU loads/stores from ROM. Compare the vendor bridge and exported
data-port clock/reset topology before adding the external AHB aperture.
Finally repeat timing, capacity/alias and cache qualification.

CPU JTAG abstract-memory reads are not automatically a cache-independent DDR
observer. The P2 script's claim of independent observation requires proof of
cache bypass or a fabric-side observer. Likewise MB/s and ns/access derived
from a hard-coded 800 MHz cycle rate need a measured cycle frequency; target
PLL configuration alone is not a measurement.

Primary specification:
[Gowin MUG1031-1.3.1E](https://www.gowinsemi.com/upload/database_doc/2725/document_ja/69811021aaeed.pdf),
Tables 3-1, 3-2, 3-3, 4-1 and 4-2.
