# Lightweight full-speed USB Host

The default is `--usb-backend ultra` in `scripts/build.py` and `scripts/cpu_qualify.py`.
The alternative `--usb-backend ohci` retains the original Spinal controller.
Disabling the USB feature removes either backend, including its PHY PLL.

```powershell
.venv/Scripts/python.exe scripts/build.py --usb-backend ultra --synthesize --output-dir build/usb-pio
.venv/Scripts/python.exe scripts/build.py --usb-backend ohci --synthesize --output-dir build/usb-ohci
.venv/Scripts/python.exe scripts/build.py --without-usb --output-dir build/no-usb
```

The retained OHCI backend and a USB-disabled minimal build both passed
compile/generate-only checks (`build/usb-light/ohci-compat` and `no-usb`);
the OHCI fallback was not reprogrammed or rerouted in this run.

The lightweight path uses Ultraembedded `usbh_host` and `usb_fs_phy` at the
existing 60 MHz system clock. The PHY sample counter has been extended to five
cycles per full-speed bit; it still works at the original 48 MHz with four
cycles. No 48 MHz USB PLL, ULPI packet wrapper, Wishbone DMA master or OHCI
descriptor engine is instantiated. Host and serial PHY are distinct RTL modules
below the `usb_host` wrapper, in separate source files. Registered bridge logic
is encapsulated by `USBPIOBridge` and emitted within that wrapper after its
reset transformation; it does not expand the SoC top.

USB3317 still boots in ULPI mode. The retained `USBPHYInit` FSM reads its ID,
writes Function Control `04=45`, OTG Control `0A=26`, Interface Control `07=09`,
then leaves STP low for six-pin serial mode. STP returns an earlier serial
session to ULPI during a local restart. F10 is still the shared PHY reset.
The 60 MHz PHY-derived PLL with configured 247.5 degree phase remains for this
initialization timing; it stays running when USB is stopped so the FSM resets.
With the RGB LCD enabled, this path allocates three PLLs rather than four.

The reference [USB3317 serial-mode notes](https://github.com/kami4ka/tang_computer/blob/main/docs/usb3317.md)
describe this serial pin mapping. This removes ULPI from packet transport;
it does not remove ULPI register initialization.

PIO registers occupy the same uncached 4 KiB window at `0xB1000000`, named
`USB_PIO_BASE`. `CONFIG_USB_ULTRA` selects the firmware adapter. The boot image
ABI includes the backend, so an OHCI image cannot silently run against PIO RTL.

TinyUSB still owns enumeration, HID interfaces, Boot keyboard/mouse decoding
and asynchronous keyboard LED requests. `hcd_ultra.c` implements a bounded
16-entry endpoint table in application DDR, one in-flight hardware packet and
64-byte PIO packet buffers. Endpoints are scheduled fairly by `hal_poll()`.
NAK waits for the next poll; duplicate DATA packets do not advance the buffer
or toggle. CRC/timeout retries are bounded, oversized packets fail safely,
and control transfers have a 500 ms deadline. Interrupt IN requests remain
pending across normal NAK idle. The main loop must keep polling and drain
the existing HID event queues.

The registered bridge latches a Wishbone request, accepts AXI AW/W separately,
and holds return data through its registered reply. Host reset also clears
bridge state and channel-valid flags. HAL leaves registers accessible until
TinyUSB invokes HCD deinit, which asserts the reset CSR. Initial attach debounce
runs with SOF disabled; SOF starts only after observing a connected device.
Removal also disables SOF so a later attach receives the same quiet debounce.

IRQ 6 counts actual hardware SOF events. DONE/ERR are polled together with SIE
idle and packet ownership because automatic SOF traffic also sets DONE.
The device-detect IRQ is not used; its upstream latch is level-triggered while
connected. Attach/detach is debounced in the main loop. Bus reset suppresses
detach detection. `hal_usb_get_info().hcca` is zero for PIO; `frame` is the SOF
counter. `test usb` checks frame progress and endpoint ownership instead of
an OHCI ED/TD chain. Existing `test usb stop/restart`, `test phys`,
`test usb input`, and `test soak 300` remain the hardware acceptance commands.

This backend supports full-speed direct devices with packets up to 64 bytes.
Low-speed 1.5 Mbit/s devices, high-speed, hubs, isochronous endpoints and bulk
throughput qualification are outside its current scope. A full-speed wireless
receiver can carry keyboard/mouse reports without requiring low-speed PHY.

Host tests: `tests/usb_ultra_hcd_test.c` exercises the actual adapter through
mocked registers, covering setup, ACK, NAK retry, short packet, duplicate
toggle, zero-length status, STALL, CRC failure, bounds and removal. PHY tests
are in `tests/usb_fs_phy_tb.v`. `sim/generate_usb_pio_bus.py` and
`tests/usb_pio_bus_tb.v` exercise the actual registered bridge and upstream
Host registers, including request cancellation by reset and recovery.
Existing Migen initialization and ULPI timing
tests continue to apply.

## Accepted lite build (2026-10-02)

`build/usb-light/final/validation.json` records the routed full-peripheral lite
build: Logic 14,890 / 20,736; registers 7,721 / 16,173; CLS 8,804 / 10,368;
BSRAM 35 / 46; PLL 3 / 4. PRIMARY and LW remain 8 / 8. Setup and hold violated
endpoints are both zero. Compare the accepted OHCI lite build at
`build/modular-full-good/validation.json`: Logic 16,275, registers 8,714,
CLS 9,131, BSRAM 36, PLL 4. This is a full-design delta, not the standalone
area of the USB core.

Final bitstream SHA256:
`ae759ea60cd8affaa2d0ef8ee852a4ce1d3113b9a8afa4068f913ac87c67217b`.
Image ABI is `50d30362`; boot ROM payload is 6,568 bytes. The monitor image
is 68,204 bytes, SHA256
`1cfb0f938e93a595241fd02ffe795334cb58d390ee88d47e3a26e3bfaed9ab67`.
The first Gowin invocation exited without diagnostics; rerunning the same
`run.tcl` succeeded. `gw-retry.log` records it. Final validation independently
rechecked ROM initialization words, image ABI/CRC, current PnR, and bitstream
hash before hardware programming.
The final HCD-only SOF/reattach fix was compiled in `build/usb-light/final-fw`;
its unchanged ROM hash, ABI and feature set were checked before refreshing the
accepted firmware directory and manifest. The bitstream was unchanged. The
earlier manifest and command logs are retained with `before-reattach` suffixes.

`firmware-verification.json` and `firmware-verification-uart.log` in that
directory record **40 command checks PASS**, including three USB restarts,
shared PHY reset, SD reads, both displays, audio, both microphones, and a
300-second concurrent soak (309.13 seconds including reporting and round completion). Receiver
`046D:C52B` enumerated all three HID interfaces. USB error/drop counters,
display underflows and concurrent audio underruns were zero. No USB unplug
or Flash programming was performed. Ethernet link was down; external network
traffic and acoustic response were not re-observed in this run. Physical USB
input and LCD behavior were checked separately below.

The current SRAM bitstream is this lite build. The UART-loaded LCD input demo
is `build/usb-light/input-demo-final/firmware/app.img`; its `test demo` passed,
as recorded in `build/usb-light/input-demo-test.log`. The user then confirmed
that the mouse cursor and keyboard text both worked normally on the physical
LCD; `build/usb-light/input-demo-observation.json` records that human observation,
separately from the automated command result. That observation preceded the
HCD removal/reattach cleanup; afterward the demo was rebuilt, reloaded and its
automated `test demo` passed again. Persistent Flash remains
the earlier release and does not select this PIO ABI after a power cycle.

## Failure record

Initial SDC paths lacked the `usb_host/` hierarchy and used `_s1` rather than
the synthesized RX first-stage `_s0` cell names, causing `TA2003` before routing.
The final exceptions reference verified first-stage cells only; all following
stages retain normal timing. Serial I/O has a 25 ns path bound; ULPI init keeps
its 4/1 ns input and 5.5/-0.5 ns output budgets.

The initial generic Wishbone/AXI bridge returned `ffffffff` on real hardware;
enumeration failed and register access delays caused audio starvation. A
registered request/reply bridge removed that symptom. The exact internal cause
of the generic bridge's behavior was not independently isolated on hardware;
do not treat the simulator's delta-cycle stall as electrical proof.

CPU IRQ enable was initially missing, and initial SOF traffic disturbed attach
debounce. Both were corrected and covered by the HCD tests. The first registered
hardware version enumerated but failed a USB-only restart because reset was
asserted before TinyUSB's final register operations and outstanding bridge state
was retained. Keeping registers live until HCD deinit and resetting the bridge
with the Host fixed the three-restart and shared-reset firmware regressions.

The removal handler originally left SOF running, which contradicted the quiet
initial attach debounce. Final review corrected it and added a mocked reattach
regression to the HCD test. Physical unplug/replug remains untested this run;
the receiver was kept connected throughout as requested.

## MMU + FPU capacity boundary

The final full-peripheral experiment at `build/usb-light/mmu-fpu-final/qualification.json`
routed with Logic 19,451, registers 10,823, CLS 10,187, BSRAM 43 and PLL 3.
It has **613 setup violations / 0 hold violations**, worst setup slack -4.548 ns;
it is not accepted for 60 MHz operation. The earlier generic bridge prototype
passed PnR but failed actual lite PIO register accesses, so it cannot qualify
the final implementation. The first registered variant failed with 101 unrouted
nets. Details and raw evidence paths are in `docs/cpu-mmu-fpu.md`.
No MMU/FPU bitstream was programmed; normal build and current hardware stay lite.
