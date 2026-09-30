# Simulation scope

Run `.venv/Scripts/python.exe sim/test_spi.py` from riscv-mini.
The Migen transaction simulation checks 8/16-bit MSB-first transfers, mode-0
clock edges, retained manual chip-select across transfers, idle clock level,
and invalid transfer length handling. The CSR path is additionally checked
by the firmware SPI loopback on hardware.

DDR training, CPU DDR access, SD file CRC, and LCD display are checked on the
board; see docs/m1-validation.md and docs/m2-validation.md. Video DMA/CDC/flip
simulation is pending M3; SPI simulation does not verify electrical timing.
