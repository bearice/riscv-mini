# Hardware DDR boot and compact ROM

Working source moves DDR initialization/training into `gateware/ddr_boot.py`.
There is no integrated SRAM. The 4 KiB ROM polls ready/failure in stack-free
assembly. On ready it initializes loader data/BSS/stack in reserved DDR
`0x407fe000..0x407fffff`, stack top `0x40800000`. Application code still starts
at `0x40800000`, with stack top `0x40c00000`.

Hardware executes JEDEC reset/release, mode registers (DLL-off CL6/CWL6), ZQ,
and two-lane training across four bitslips and 256 delay settings, checking
three seeds (42/84/36), read-data-valid and burst detect. It picks the widest
window midpoint and verifies it. Scratch is bank0/row0/column0. Controller and
crossbar stay reset; masters are gated until handover. Failure stops commands;
ROM reports failure/timeout without accessing a DDR stack.

Full SoC reset now resets DMA/peripherals and restarts training together, while
keeping PHY init/DLL clock alive. A final candidate must qualify software reset
with this connection; the earlier working candidate did not retrain on CPU reset.

## Loader size

Loader links startup, UART, time, compact loader-only Flash and main with
`-Os -flto`. SD, display, filesystem and Flash UID remain in the DDR app.
Bounds, ABI, UART framing, CRC, finite timeouts and header-last Flash install
remain. There is no post-write Flash readback verification.

| Snapshot | Bytes | Evidence |
| --- | ---: | --- |
| Previous software DDR boot | 6584 | `build/soc-layout/release/validation.json` |
| First hardware DDR boot | 4712 | `build/ddr-hw/boot-size-before.json` |
| Compact, no C/B | 4024 | `build/ddr-hw/boot-size-compact.json` |
| C only | 2856 | `build/ddr-hw/boot-size-c-only.json` |
| C + Zba/Zbb/Zbs | 2852 | `build/ddr-hw/boot-size-cb.json` |

C gives nearly all the ROM savings; B saves four additional bytes. Reproduce
function/section analysis with `scripts/boot_size.py ELF --json REPORT`.
LTO inlined callees are attributed to their caller.

| Function | First HW loader → C+B bytes |
| --- | ---: |
| main (includes receive/install/probe) | 1552 → 938 |
| flash_program | 336 → 194 |
| from_flash | 272 → 134 |
| flash_read | 264 → 100 |
| _start | 224 → 190 |
| image_valid | 204 → 142 |
| execute | 144 → 78 |
| uart_byte | 144 → 90 |
| write_enable | 120 → 62 |
| ready | 112 → 82 |
| status | 96 → 46 |
| image_crc | 80 → 56 |
| uart_read | 76 → 46 |
| io_puts | 52 → 28 |
| io_putchar | 28 → 18 |
| io_ticks | 24 → 16 |

Former transfer (176 bytes) becomes byte (90) plus address_command (74).
io_hex (100) is no longer linked. JSON reports include exact symbols/padding.

## Verified boundaries

First `build/ddr-hw/release` used ROM6KiB, SRAM0, loader4712, BSRAM40/46,
PnR setup0/hold0 and passed board firmware commands. ROM4KiB candidates require
fresh checks. `tests/ddr_boot_test.py` checks fake-PHY JEDEC/training/failure,
missing data-valid/burst and reset; electrical timing is outside simulation.
Hardware training does not replace the removed software startup data-bit,
address-alias and three-region memory tests. `test ddr` covers 8 KiB, not 128 MiB.

Original C+B PnR had 293 setup violations. Two-cycle I-cache plus injector
reduced this to six, still not deployable. New C-only/no-C plus L2 experiments
are described in [l2-cache.md](l2-cache.md). Failed-timing candidates are not
programmed by the normal upload tool.

The final no-C/L2 candidate now qualifies the 4KiB-ROM/no-SRAM boot flow,
five full software resets, UART error boundaries and a five-minute external
network/audio/SD/USB/display run. See the L2 page for exact evidence. Its
4024-byte ROM leaves72 bytes; the C-only/L2 ROM is2888 bytes but not accepted
because both board attempts stopped at the preready DDR error path. Persistent
Flash remains unchanged; this trial runs FPGA SRAM plus UART-loaded application.
