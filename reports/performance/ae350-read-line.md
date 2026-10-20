# AE350 memory-domain read-line optimization

The adapter returns one 256-bit block through the response FIFO. Subsequent valid beats of the same read burst select their data in the mem domain. UI-side read reuse state is removed; independent NONSEQ accesses still fetch DDR. The registered interconnect boundary and write aggregation remain.

## Conditions and identity

Candidate gateware: `8ac7e645d40cf4d5db6ef4672a1244a9c0bfeba93a27f28c10cb645f02d8646f`; payload SHA-256: `ff49f05277c6422ac6ede1038f4da9c089794b62ed314e80f3c09c081cc5bcc1`. The diagnostic binary matches the original baseline; the board uses its current ABI-compatible payload receipt. The old receipt is correctly rejected by the host ABI check.

Baseline: [original memory baseline](ae350-memory-baseline.md), unchanged. Three board runs, three samples per mode per run (nine samples per row), 64 KiB per sample, ILM code and DLM stack; timed writes/copies include final clean/fence. Clocks: sys 80 MHz, mem/UI 100 MHz, DDR CK 400 MHz. Counter-clock estimate: 800.513 MHz.

## Results

| Mode | Operation | Baseline cycles | Candidate median | Cycles reduction | Estimated MiB/s | UI reads / writes |
|---|---|---:|---:|---:|---:|---:|
| uncached | write | 1835103 | 1704027 | 7.1% | 29.36 | 0 / 16384 |
| uncached | read | 7378769 | 7116645 | 3.6% | 7.03 | 16384 / 0 |
| uncached | copy | 9961311 | 9684835 | 2.8% | 5.17 | 16384 / 16384 |
| write-back-cold | write | 1973777 | 1521993 | 22.9% | 32.87 | 2048 / 2048 |
| write-back-cold | read | 1470535 | 1069019 | 27.3% | 46.80 | 2048 / 0 |
| write-back-cold | copy | 3491233 | 2655801 | 23.9% | 18.84 | 4096 / 2048 |

Cold reads improve because later refill beats avoid the CDC round trip. Cold writes also perform write-allocate reads. UI command counts stay unchanged. Uncached reads still issue 16,384 commands, so their smaller improvement is consistent with the simplified first-beat UI path, not cross-NONSEQ hits. The cold-read difference is about 196 CPU cycles per refill; this agrees in scale with the expected savings but does not precisely isolate CDC cost.

Dependent loads (pooled median of all nine raw samples, DDR-resident code, not the ILM bandwidth measurement): 1731152 cycles median versus 1780560 baseline for 1,024 hops. Instruction-fetch contention is included.

## Qualification and limits

JTAG identity/POST, DDR suite (13 checks, zero failures; 1 MiB traversal; 2-second stress) and DMA suite pass. The three performance runs use flags 5, hence 12 checks each; they do not include traversal. Only FPGA SRAM was programmed.

STA: setup/hold/minimum-pulse violations 0/0/0; minimum pulse slack +0.183 ns. Mem Fmax 100.100 MHz; worst setup slack +0.010 ns from response FIFO through lane selection into resp_data. This passes the current gate with little margin for further edits. A separate first-beat latch stage is an untested fallback, not part of this candidate.

New diagnostics at DDR CSR +0x44 (accepted read-line hits) and +0x48 (adapter HREADY-low cycles) wrap at 32 bits and are exported together using a coherent CDC snapshot. They are not reset-on-read or a transaction-specific latency histogram. They do not change image_abi().

This is incremental qualification, not new full-capacity/long-stress qualification. The original baseline and catalog current remain unchanged. Raw samples, receipt hashes, clock brackets and limitations are in [the JSON report](ae350-read-line.json).

## Board evidence

- `build/runs/20261020T094746064778Z-board-ae350-jtag-test/result.json`: ae350-jtag-test, PASS.
- `build/runs/20261020T095157927218Z-board-ae350-ddr-suite/result.json`: ae350-ddr-suite, PASS.
- `build/runs/20261020T095413237536Z-board-ae350-dma-suite/result.json`: ae350-dma-suite, PASS.
- `build/runs/20261020T095503782700Z-board-ae350-ddr-suite/result.json`: ae350-ddr-suite, PASS.
- `build/runs/20261020T095545125606Z-board-ae350-ddr-suite/result.json`: ae350-ddr-suite, PASS.
- `build/runs/20261020T095626426074Z-board-ae350-ddr-suite/result.json`: ae350-ddr-suite, PASS.

The current-source diagnostic artifact `90e68ee8d8ec3ab3fa735368b8e4a3e9a2f9c640d058f8371fd87fc5364907bb` was rebuilt and passed 13 checks with zero failures in `build/runs/20261020T100856282108Z-board-ae350-ddr-suite/result.json`. Its binary is byte-identical to the performance payload. This closes the source-input mismatch in the older payload artifact; original measurement receipts are retained without modification.
