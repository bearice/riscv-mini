# AE350 UART verification — 2026-10-18

The UART failure is currently cleared. The unchanged `c1633c35ced7` candidate
delivers its banner, accepts an upload, acknowledges five packets, and executes
the uploaded payload from DDR after the user unplugged and reconnected the
board USB. The original failure's precise cause is not established. Do not
describe it as a confirmed FPGA TX pin, AE350 UART, or electrical failure.

Evidence directory: `build/runs/20261018T113054Z-uart-investigation/`.

## Identity and initial observations

- Board USB serial `2025030317A`: FPGA JTAG, location `6705`, COM7.
- Board USB serial `2025030317B`: UART, location `6706`, COM9.
- Independent CPU JTAG: location `107569`, TAP `0x1000563d`.
- Initial CPU PC `0x80000184`; `mcause/mepc/mtval` all zero. POST reported
  completion and no errors. UART LCR `3`, OSCR `16`, LSR `0x60`.
- With the CPU halted, UART2 MCR internal-loopback bit was enabled temporarily.
  Writing `0x55` to DATA produced LSR `0x61` and DATA `0x55`. MCR and FIFO were
  restored afterwards. This verifies the UART engine and its internal loopback,
  not the P15-to-host electrical path. See `cpu-loopback.json`.
- The Dock schematic contains a BL616 USB/JTAG/UART bridge. FTDI-compatible USB
  enumeration alone is not evidence that the board has a native FT2232 chip.

## Probe side effect and recovery

A direct D2XX UART probe in this investigation blocked while using the bridge;
its buffered output did not establish the exact blocking API. The probe
included `FT_GetBitMode` before timeout setup. Terminating the probe did not
immediately release COM9. Windows `pnputil /restart-device` returned access
denied. This was an investigation-induced additional failure, not evidence
about the cause of the earlier TX silence.

The user clarified that the board has no independent power supply, then
unplugged and reconnected the board USB after the recovery artifact had been
checked. Treat this as a board power cycle as well as a USB bridge reset;
the two effects cannot be separated by this experiment.

## Same-candidate result after reconnect

SRAM-only programming, no Flash writes:

- Gateware: `c1633c35ced7872e5458f8619a330dcf6fc83f2bf39339b0d70f119f02712421`.
- FS SHA256: `1a6fca17a80e77ab277ecf77daa8a3b9b0a45b54b3e43ac6047ea1ee8e1fc7e9`.
- Timing: setup/hold `0/0`.
- Bootloader artifact: `23d8eea024e74696c50ca40073b1f7397ad22c8379dc9e640e43c79047703035`.
- ROM build string: `c4bd4159b9f5`; complete ROM readback matched this artifact.

COM9 at 115200 received:

```text
riscv-mini AE350 ROM c4bd4159b9f5
POST 600d0000
DDR INIT OK
BL>
```

Sending `u` returned `READY HEADER`. The upload probe subsequently received
`READY DATA`, all five `K + uint32_le(sequence)` acknowledgements (0–4), and
`SYSTEM READY`. It uploaded 536 bytes to DDR `0x3000`; CPU PC was `0x3014` in
the payload's terminal loop, and SRAM `0x40001980` contained the payload's
expected marker `0x55415254`. Reading back all 536 bytes from DDR matched the
uploaded payload exactly. JTAG was used to verify ROM identity, restart ROM,
and inspect results; it did not load the payload or publish a mailbox command.

Files: `sram-after-reset-correct-cable.log`,
`uart-after-reset-correct-cable.bin`, `uart-u-after-reset-correct-cable.bin`,
`uart-upload.log`, `uart-upload.json`, `uart-probe-payload.bin`.

The CPU was returned to ROM entry after the probe. The selected candidate is
still in SRAM. No source fix or new bitstream was required to restore UART.

## Independent software findings

1. **The loader is not yet wire-compatible with the 20K uploader.** Current
   AE350 ROM reads `uint32 magic(0x4b30), uint32 sequence, uint32 length, data`.
   `scripts/boot_image.py::packet()` emits `uint32 sequence, uint16 length,
   data, uint32 packet_crc`. Matching 48-byte header size and ACKs does not make
   these interchangeable. AE350 also does not presently perform the same header
   CRC/ABI/entry validation as the existing host format. The successful probe
   used the current AE350 framing explicitly; it does not qualify the existing
   20K host uploader. Align the actual framing and its validation before claiming
   that requirement complete.
2. **Automatic cable selection is unsafe with both debuggers present.**
   `scripts/programmer_cable.py` interprets the number after `USB Debugger A/`
   as the channel and selects `0`. The live scan reported index 0 at `107570`
   (independent debugger channel B), while board FPGA JTAG was index 1 at
   `6705`. The wrong attempt was stopped; the successful SRAM programming used
   explicit `--location 6705`. Do not infer the physical channel from list index.
3. **One existing fabric UART probe has a baud-rate mismatch.**
   `build/work/uartprobe/build.py` instantiates `UartTx()` with its default
   50 MHz divider in the 100 MHz `sys` domain. Its nominal 115200 stream is
   consequently about 230400. Unless the earlier fabric comparison used a
   different corrected probe, silence at 115200 is not a valid independent
   transmitter comparison.

This investigation qualifies current UART banner, RX, TX, packet ACKs, upload,
and DDR execution of the small probe. It does not rerun the complete P2 memory
suite, establish the original intermittent failure's root cause, or qualify
the loader's ABI/security/range validation.
