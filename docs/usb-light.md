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
compile/generate-only checks; the OHCI fallback was not reprogrammed or
rerouted in that run.

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

The USB Host IRQ (number 6 in the default full-peripheral build; numbering
follows LiteX peripheral add order, see [HAL](hal.md)) counts actual hardware
SOF events. DONE/ERR are polled together with SIE
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

## 当前限制

Flash 启动曾出现 PHY ready、SOF 正常但 HID 未枚举的间歇失败；`test usb restart` 可恢复，根因尚未定位。驱动 errors=0 不代表设备连接成功。HAL 调用者需要持续 `hal_poll()` 并消费队列。默认 full 的硬件资源见 [系统设计](system-design.md)。
