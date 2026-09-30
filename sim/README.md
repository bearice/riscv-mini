# Simulation scope

Run `.venv/Scripts/python.exe sim/test_spi.py` from riscv-mini.
The Migen transaction simulation checks 8/16-bit MSB-first transfers, mode-0
clock edges, retained manual chip-select across transfers, idle clock level,
and invalid transfer length handling. Loopback CSRs were removed from the
base SoC; actual Flash, SD and SPI LCD transactions are verified on hardware.

DDR training, CPU DDR access, SD file CRC, and LCD display are checked on the
board; see docs/m1-validation.md and docs/m2-validation.md. SPI simulation
does not verify electrical timing.

`sim/test_video.py` checks full 480x272 LCD scan periods, sync pulse widths,
pixel order and last-marked frame recovery after an injected pixel underrun.
This covers the scanner, not real DDR arbitration or connector electrical timing.

`sim/test_memory.py` runs the actual shared native-port scheduler with two
callers, delayed single-cycle replies, changing data after the reply, and
consumer backpressure. It verifies CPU/video response ownership, write data
and byte enables, and transaction serialization. It does not model the DDR
PHY or prove electrical timing. Historical M4 stress results are recorded in
docs/m4-validation.md; current base acceptance is in docs/base-validation.md.

`sim/test_boot_image.py` verifies the host image header, CRC, truncation,
entry/load boundaries and UART packet framing. `scripts/boot_verify.py` also
exercises rejection by the actual ROM loader and execution in real DDR.

`sim/test_programmer_result.py` rejects Gowin's observed zero-exit-code
`SPI Verify failed` output as well as missing completion/nonzero return codes.
