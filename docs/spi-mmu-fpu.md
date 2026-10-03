# SPI SD + full MMU/FPU qualification

2026-10-02: full peripherals, CSR packing, PIO USB, same baseline MMU/FPU RTL;
only SD backend changes native -> SPI. sys/CPU60 MHz, DDR120 MHz. No retiming,
place option2, route option1. Default SoC backend remains native, CPU remains lite.

## Results

| Resource | Native synthesis | SPI synthesis | Change |
|---|---:|---:|---:|
| Logic | 19147 | 16380 | -2767 |
| Register | 10802 | 9713 | -1089 |
| BSRAM | 43 | 41 | -2 |

SPI PnR passed: Logic16572/20736, FF9745/16173, CLS9371/10368,
BSRAM41/46, DSP3.75/24, PLL3/4. Setup0, Hold0, reported worst setup
slack +0.944 ns. Critical reported path starts CPU memory_arbitration_isValid
and ends CsrPlugin_mtval, through multiply/divide and FPU pipeline controls.
Do not equate native synthesis with PnR values. The previous native full PnR
failure was before CSR packing; no post-packing native PnR was run here.

The SPI8 controller itself is 89 Logic (74 LUT+15 ALU), 71 FF; card detect
uses 2 FF. Shared bus and CSR also change, so the actual whole-design saving
is 2767 Logic rather than subtracting only protocol module sizes.

## Reproduce

```
.venv/Scripts/python.exe scripts/cpu_qualify.py --cpu-verilog build/cpu-qualification/reproduce-generator/VexRiscv_MmuFpu.v --sd-backend spi --output-dir build/csr-packed/mmu-fpu-spi
.venv/Scripts/python.exe scripts/build.py --sd-backend spi --output-dir build/csr-packed/spi-firmware
```

CPU RTL SHA256: `45333b754ab68726f6eb23ede586af40cb5f2d7990569a60fcc8b6f9cd45fc97`.
Evidence: `build/csr-packed/mmu-fpu-spi/qualification.json`, `comparison.json`,
`gateware/impl/gwsynthesis/project_syn.rpt.html`, `gateware/impl/pnr/project.rpt.txt`,
`project_tr_content.html`, `project.timing_paths`, `cmd.do`, and
`build/csr-packed-spi-retry.log`.

## Build fix and boundaries

Initial synthesis failed EX3884: hierarchical instance sd_detect shadowed its
input port sd_detect. soc.py now renames the pad sd_detect_pad; physical pin,
CSR bank and C accessors remain unchanged. Initial failure is saved in
`build/csr-packed/mmu-fpu-spi/naming-failure.json` and `build/csr-packed-spi.log`.
Retry completed with no synthesis/PnR errors. cpu_qualify.py now exposes
--sd-backend and records it in the qualification report.

SPI full firmware compiled with the default lite CPU: boot6568 bytes, app image
67748 bytes, ABI0xfd67add7 (`build/csr-packed/spi-firmware/validation.json`).
This is compilation evidence for the SPI HAL, not MMU/FPU-adapted software.
SPI driver still uses software CRC and byte PIO at6 MHz; no new throughput,
SD card operation or coexistence hardware test was performed.

MMU/FPU qualification embeds the historical boot ROM for realistic resources.
It does not establish D-cache/DMA coherence, FPU initialization or execution,
Sv32 page-table/privilege behavior, or boot correctness. Do not program this
experimental bitstream with existing firmware as an accepted system. No board
or Flash programming occurred. Keep-awake helper was stopped after completion.
