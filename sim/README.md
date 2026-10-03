# Simulation scope

`sim/test_features.py` verifies peripheral dependencies and rejects images for
a different feature configuration even when CSR addresses match. `sim/test_rtl.py`
checks hierarchical SDC targets for every bit of a vector synchronizer, reset
nets and external USB core instances. `scripts/build_matrix.py` compiles the
29 supported on/off/backend cases and checks their CSR/pad presence; it does
not prove routed timing or hardware behavior.

`sim/test_microphone.py` drives the actual I2S receiver with distinct positive
and negative left/right PCM24 words and deliberately nonzero delay/padding bits.
It checks the one-bit I2S delay, 64 BCLK/frame, clock divider, sign extension,
two bounded snapshots, independent DA inputs paired in the same frame, identical
clock outputs and LR=0/1 in stereo, FIFO draining and stop/cancel. It does not identify the
physical microphone or verify electrical timing/acoustic response.

Run `.venv/Scripts/python.exe sim/test_spi.py` from riscv-mini.
The Migen transaction simulation checks 8/16-bit MSB-first transfers, mode-0
clock edges, retained manual chip-select across transfers, idle clock level,
and invalid transfer length handling. Loopback CSRs were removed from the
base SoC; actual Flash, SD and SPI LCD transactions are verified on hardware.

DDR training, CPU DDR access, SD file CRC, and LCD display are checked on the
board; see docs/hardware-ddr-boot.md and docs/system-design.md. SPI
simulation does not verify electrical timing.

`sim/test_video.py` checks full 480x272 LCD scan periods, sync pulse widths,
pixel order and last-marked frame recovery after an injected pixel underrun.
This covers the scanner, not real DDR arbitration or connector electrical timing.

`sim/test_memory.py` runs the actual shared native-port scheduler with two
callers, delayed single-cycle replies, changing data after the reply, and
consumer backpressure. It verifies CPU/video response ownership, write data
and byte enables, and transaction serialization. It does not model the DDR
PHY or prove electrical timing. Current board acceptance for shared-port
coexistence is recorded in docs/system-design.md.

`sim/test_boot_image.py` verifies the host image header, CRC, truncation,
entry/load boundaries and UART packet framing. `scripts/boot_verify.py` also
exercises rejection by the actual ROM loader and execution in real DDR.

`sim/test_programmer_result.py` rejects Gowin's observed zero-exit-code
`SPI Verify failed` output as well as missing completion/nonzero return codes.

`sim/test_board_io.py` covers logical LED polarity/order, DIP synchronization,
button bounce suppression, held-key/no-repeat, release and simultaneous keys.
It also samples actual 60 MHz WS2812 output: GRB MSB order, 24/48-cycle high
pulses, 75-cycle bit period, 18000-cycle latch and ignored submissions while busy.
These checks do not replace real board switch/LED observation.

`sim/test_sd.py` checks real SDCore good/corrupt command CRC and PHY timeout
status propagation, plus 400 kHz/15 MHz/7.5 MHz SD clock periods at sys 60 MHz.
`--upstream` reproduces the pinned upstream CRC visibility defect (expected failure).
It uses a PHY endpoint seam and does not prove connector timing.
The actual SDCore descriptor FIFO is also exercised with early request arming,
long PHY stalls, exactly three read blocks and timeout/abort flush. This checks
the registered request seam added for M9 timing without replacing the core.
`scripts/sd_verify.py` runs the independent SD demo on the board: existing file
CRC, single/multi-block and unaligned callers, optional unique-file write/read,
and five-minute LCD frame switching. See docs/native-sd.md.

`sim/test_audio.py` decodes actual BCK/WS/DIN for stereo sample/bit order,
including the current DDS 48k clock enables and the legacy integer-divider path.
underrun zeros and mute. It checks real 60MHz clock enables (1.5MHz BCK /
46875Hz frames), DMA word order, ring wrap/ownership, request release,
stop during a delayed DDR reply, framebuffer boundary rejection and bus errors.
`sim/test_audio_capture.py` tests the WAV analyzer with a surviving DC offset
and requires actual suppression of the test tone; an unmuted tone fails.
`scripts/audio_verify.py` runs the independent audio demo: PIO underrun,
DMA/pause/resume/stop, optional Windows line-in capture, and five minutes
of muted PCM DMA + SD file CRC + DDR LCD frame switching. See docs/audio.md.

`sim/test_ethernet.py` exercises the real LiteEth Wishbone packet SRAM:
little-endian RX words, two-slot ownership, queue-full/CRC/oversize drops,
TX slot separation, partial last words under backpressure, and local reset
clearing queued work. It also verifies Gowin synchronizer preservation attributes.
It does not prove RMII electrical timing. The independent Ethernet app checks
ARP/IP/ICMP/UDP parsing on the real CPU; `scripts/ethernet_verify.py` checks
Flash UID-derived MAC against host computation/ARP, UDP payloads through MTU,
ping, shared reset, and at most five minutes of SD/display/muted-audio coexistence.
Raw slot drops and actual UDP timeouts are reported separately. See docs/ethernet.md.

`sim/test_bus.py` exercises the registered Wishbone request/reply seam with
delayed transfers, byte enables, bus errors and an abandoned transaction.
`sim/test_usb_phy.py` exercises the USB3317 initialization FSM: ID read data
with NXT low, turnaround, DIR preemption/retry, STP write termination and
six-pin serial output mapping. `sim/test_usb_ulpi_timing.py` adds a quantized
edge/delay model at two propagation corners; these checks do not prove USB
electrical timing or HID input. `scripts/ethernet_verify.py --usb` checks the
connected FS HID receiver, three local software restarts, shared F10 reset,
DDR HCCA alignment, OHCI progress and <=5 minute media coexistence on hardware.
Physical keyboard transitions remain a separate acceptance step. See docs/usb.md.

`sim/test_build_report.py` checks routed resource extraction, including full
PLL/global clock usage, setup/hold violations and rejection of incomplete reports.
It uses captured resource rows and temporary reports; it does not run synthesis.

Run all local checks from the repository root (no board or programming required):

`tests/ddr_boot_test.py` additionally covers hardware training against a fake
PHY. `tests/l2_test.py` checks the write-through shared cache against delayed
wide memory, including byte writes, bypass, abort and reset. These do not
substitute for Gowin RAM mapping, PnR or board acceptance.

```powershell
Get-ChildItem sim/test_*.py | ForEach-Object {
    & .venv/Scripts/python.exe $_.FullName
    if ($LASTEXITCODE -ne 0) { throw "Failed: $($_.Name)" }
}
```
