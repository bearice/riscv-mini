# M9 USB Host

Dock U6 is USB3317 with a 26 MHz crystal and 60 MHz ULPI CLKOUT (T15).
DATA[0:7] is G11/H12/J12/H13/T14/R13/P13/R12; STP/DIR/NXT is K11/K12/K13.
F10 resets both USB3317 and Ethernet RTL8201F and has one GPIO owner.
The USB OTG connector is separate from the BL702 debugger/serial connector.

The Host uses the pinned Spinal OHCI core at 48 MHz (third PLL), with Wishbone
control and DMA at sys 60 MHz. CPU and DDR remain 60/120 MHz. USB3317 is first
read/configured over ULPI and then operates in six-pin serial mode, retaining
its 60 MHz clock. A fourth PLL regenerates the ULPI 60 MHz clock to meet
FPGA output setup (225-degree configured phase); its lock is part of the initialization reset. All four
PLLs are allocated. The current PHY configuration is full speed, 12 Mbit/s;
high-speed USB, hubs, mass storage and arbitrary report-format keyboard
decoding are outside this milestone. Generic HID reports remain available raw.

Register initialization reads Vendor/Product ID `0424:0006`, writes Function
Control `04=45`, OTG Control `0A=26`, and Interface Control `07=09`. Register
reads skip the bus turnaround and capture data with NXT low. Writes retry on
DIR preemption and terminate with STP, including serial-mode entry. STP also
returns a previous serial session to ULPI for local restart. HAL initialization
has a 150 ms PHY deadline and OHCI reset/ownership waits are bounded.

OHCI registers occupy `0xB1000000..0xB1000FFF`. HCCA (256-byte aligned), ED/TD
lists, enumeration buffers and HID buffers are in application DDR. No extra
CPU SRAM ring is required. The VexRiscv lite core has no data cache; fences
protect DMA handoffs. IRQ 6 queues TinyUSB events; enumeration and HID callbacks
run from `hal_poll()`. Applications must call it frequently and drain queues.
The API has one main-loop owner. Raw queue capacity is 8 reports of at most
64 bytes; key queue capacity is 32 transitions. Overflows are counted.

`hal_usb_init/stop/poll/get_info` manage the controller. `hal_usb_report_take`
provides raw HID reports; `hal_usb_key_take` provides standard USB usages,
press/release and modifier bits for Boot keyboard interfaces. Modifier keys
also generate E0..E7 usage transitions. Identical reports do not repeat keys;
rollover reports preserve previous state. Character layout/repeat belongs to
the application. TinyUSB requests Boot protocol for boot-capable interfaces.
`hal_usb_mouse_take` supplies Boot mouse buttons and relative X/Y/wheel motion;
`hal_usb_keyboard_leds` asynchronously submits a Boot keyboard LED mask.
Mouse and keyboard event queues each hold 32 events. LED transfer storage stays
owned until completion; another LED update returns BUSY during that transfer.

`hal_phys_reset` stops both controllers, resets F10, and rebuilds Ethernet and
USB. A USB-only restart uses STP and does not reset F10 or Ethernet. Reboot
stops USB before returning to ROM. `hal_usb_get_info` exposes PHY status,
VID/PID, interfaces, report/event counters, errors, root-port status and HCCA.

M10 keeps the ULPI PLL running while the USB enable CSR is zero, so the
synchronous initialization FSM can clear ready/ID. Resetting that PLL on
disable was observed to leave ready=1 after repeated stops. F10 still resets
the PLL; the domain's enable/reset/lock logic still holds the FSM in reset.
The regression uses the real `test usb stop/restart` firmware commands;
PHY FSM simulation alone cannot reproduce a stopped physical PLL.

Current on-board commands are `test usb`, `test usb stop/restart`,
`test usb input`, `test usb leds ...` and `test phys`. Batch execution uses
`scripts/firmware_verify.py`; input and physical LED acceptance remain manual.
See [firmware commands](firmware-tests.md) and [M10 review](m10-review.md).

The base monitor prints key usages over UART. The independent
`firmware/examples/ethernet_demo.c` adds `u` for USB diagnostics and `j` for
USB-only restart to its SD/LCD/audio/network acceptance workload. Run
`scripts/ethernet_verify.py --usb` with the existing source NIC/IP and
`--soak-seconds 300`; it checks three local restarts, shared PHY reset,
OHCI frames/HID presence, UDP, SD CRC, LCD scanning and muted audio DMA.
It never asks for or requires physical USB removal.
`--settle-seconds 30` explicitly separates post-reset host-network background
traffic from the steady-state soak. It defaults to zero, is recorded outside
the soak window and is not a UDP retry. Immediate post-reset UDP loss on the
unfiltered two-slot Ethernet receiver remains a known limitation; see
[M9 validation](m9-validation.md). Physical keyboard input is postponed.

Simulation: `.venv/Scripts/python.exe sim/test_usb_phy.py` exercises the actual
initialization FSM. It does not prove electrical USB timing or enumeration.
`sim/test_usb_ulpi_timing.py` adds a quantized edge/propagation model for
command/data/STP sequencing and read turnaround at two timing corners; this
behavioral probe supplements, and does not replace, post-route STA.
`sim/test_bus.py` checks the registered DDR/OHCI Wishbone seams with delayed
responses, byte enables, errors and canceled requests. ULPI input sampling
uses falling edges; initialization output data/STP is registered. Serial RX
uses two synchronizer stages before the controller. Timing constraints retain
the second-stage and all normal controller/CPU/DDR paths. Spinal's held TX
payload has a 20 ns maximum path bound and no asynchronous hold requirement.
RX bytes stay stable until the next USB byte; their FlowCC capture has a
16 ns path bound, with its synchronized valid toggle gating consumption.
ULPI I/O uses the PHY's external 60 MHz clock: input max/min 4/1 ns, output
max/min 5.5/-0.5 ns. The phase-shifted initialization outputs target the next
external rising edge after internal clock insertion; their setup-only
multicycle constraint retains the intervening hold check. Serial TX has an
explicit 25 ns launch-to-pad bound, including output and clock-insertion budget.
Historical M9 acceptance and identity are in m9-validation.md; current M10
reset fixes and command acceptance are in m10-review.md.

Primary references: [USB3317 datasheet, §§4.3,6.2,6.3,7.1](https://ww1.microchip.com/downloads/aemDocuments/documents/UNG/ProductDocuments/DataSheets/00002366A.pdf),
[Spinal OHCI](https://github.com/litex-hub/pythondata-misc-usb_ohci/tree/17c1d3d6548ea267e19aec3cb6d2e64335a1bb2a),
[TinyUSB OHCI/HID Host](https://github.com/hathach/tinyusb/tree/3af1bec1a9161ee8dec29487831f7ac7ade9e189/src).
