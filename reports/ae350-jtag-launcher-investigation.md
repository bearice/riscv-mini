# AE350 JTAG launcher investigation — 2026-10-18

The launcher failure is reproduced and fixed. The immediate fault is stale ROM
responses from the fabric ROM adapter, not a proven DM/core SRAM alias or an
FTDI-driver defect. No Flash writes, driver changes, or current-reference changes
were made.

## Reproduction and diagnosis

Initial live snapshot: `build/runs/20261018T065124Z-jtag-investigation/`.
The CPU is in the trap handler, with mcause=2, mepc=0x800000c0,
mtval=0, DPC=0x800000a4. Thus this snapshot is not a normally polling monitor.
Cache control CSR 0x7ca reads zero.

Repeated abstract reads of nonsequential ROM addresses produce data associated
with previous requests. For example, ROM address 0x80000000 returns different
instruction words depending on the previous address; SRAM 0x1800 reads back
the published command magic consistently. A sequential ROM dump is shifted
by one word relative to the selected boot.bin: its first real instruction
0x30401073 appears at dump offset four. These are direct observations of the
response defect, not a conclusion based only on a stalled mailbox.

The prior AE350ROM wraps synchronous AHBMemory with a second phase counter and
response register, but does not couple its ready/data capture to the underlying
memory transaction. It also chooses status words using the live address rather
than the accepted transaction address. The outer ready can therefore expose
the previous synchronous ROM word. Corrupted instruction fetches also explain
why resuming the CPU cannot reliably execute the mailbox polling loop.

`scripts/tests/test_ae350_rom_response.py` reproduces stale data before the fix:
reading offset 4 returns offset 0's instruction, reading offset 0 then returns
offset 4's instruction, and further nonsequential reads repeat this pattern.

## Repair

AE350ROM now has one transaction state machine: latch the address, allow the
synchronous memory read, capture the data/status response, then assert ready.
The response mux uses the latched address. Completion can accept the next
address; rejected writes/out-of-range reads retain a two-cycle ERROR response.
Byte offsets still return the containing 32-bit word, as required by the
HSIZE-less hard ROM port.

The existing native-memory test now allows the additional registered read
latency while retaining its byte-address, bounds and write-rejection checks.
No speculative internal-memory remapping or alternate mailbox is needed.

## Reproducible board operation

The supported entry point is:

```text
.venv/Scripts/python.exe scripts/mini.py board jtag-test --build <exact-build-run/result.json> --location 6705 --cpu-jtag-location 107569
```

Locations identify the currently connected adapters and must be checked again
after re-enumeration. The operation checks the immutable candidate and 0/0
timing, programs SRAM using GW5AST-138B, verifies every byte of the ROM and
nonsequential reads, loads a self-contained RV32I probe at 0x1000, publishes
the mailbox at 0x1800, resumes, and halts before every abstract-memory poll.
The code uses no stack, stores results at 0x1900, and completion at 0x1940.
It writes eight distinct DDR values including four positions in one block.
The generated probe bytes and all expected/actual values are retained with
the operation result. Cleanup clears the mailbox and resumes at the ROM entry.

Initial repaired candidate:
`d894a5e387ab8be0ead17e2c16c11ca1c8d2bdb1238a0d491ccea71a4474daa7`,
FS SHA256 `65d38ff897b34ab411ee866699773c3a4ba0440898d233b852f0786419e829e8`,
PnR setup/hold 0/0. Two independent SRAM programming cycles passed:

- `build/runs/20261018T065526435403Z-board-ae350-jtag-test/result.json`
- `build/runs/20261018T065617222770Z-board-ae350-jtag-test/result.json`

Both report full 292-byte ROM match, nonsequential read match, launcher ACK
0xa55a0001, completion 0xd0000002, CPU DDR results 8/8, and zero
mcause/mepc/mtval. The ROM monitor is observed at 0x80000100 before launch.

An earlier operation `20261018T065509434601Z-board-ae350-jtag-test` failed at
device-ID checking because the reused legacy programming helper selected
GW2A-18C. It did not program the target. The new operation now explicitly uses
GW5AST-138B and does not change the legacy 20K helper.

Final candidate (also retains the explicit two-cycle ROM error response):
`658d75c2195007ac4e271d8edd0cc7215e3c733dde8227020a86c507d950271e`,
FS SHA256 `6609f38dfeac477dbf114c89da885b699fac4b80707359d43e20fbbff65bd0c5`,
PnR setup/hold 0/0, built by
`build/runs/20261018T065838683259Z-build-candidate-f8ed1b/result.json`.
Final board verification:
`build/runs/20261018T070044573768Z-board-ae350-jtag-test/result.json`, PASS.
Full ROM identity, nonsequential reads, mailbox ACK, completion marker and all
eight CPU DDR results pass again, with zero mcause/mepc/mtval.
Post-cleanup snapshot in the same directory confirms the CPU at ROM polling
PC 0x80000100, SP 0x2000, GP 0x800, cleared mailbox, DDR status 0x203,
and zero exception CSRs. The CPU was resumed after that observation.

The preceding final-build attempt
`20261018T065647376937Z-build-candidate-2d28e5` failed while atomically
publishing its output directory with Windows error 5. It is not a successful
build receipt. Retrying the supported build entry point completed normally.

## Scope

Host verification: the new ROM response regression passes, native memory tests
pass (3), JTAG safety tests pass (3), board-operation unit tests pass (38), and
the existing DDR adapter interface-stub suite passes. The P1 artifact-specific
checker is not applicable to this P2 wrapper/DDR build and was not used as P2
qualification.

These results establish the JTAG-to-ROM-to-SRAM-launcher-to-CPU-DDR basic
read/write path. They do not qualify full capacity/aliasing, cache maintenance,
DDR execution, arbitrary timeout recovery, CDC, performance, or long stress.
UART was not tested or modified. Existing P2 report history remains evidence
for its own source versions only.
