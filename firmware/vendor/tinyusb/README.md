# TinyUSB Host subset

Upstream: https://github.com/hathach/tinyusb, release **0.20.0**, commit
`3af1bec1a9161ee8dec29487831f7ac7ade9e189`. MIT license is in LICENSE.

Only common utilities, OS abstraction headers, host headers/core, HID Host and
OHCI are vendored. The application compiles `tusb.c`, `tusb_fifo.c`, `usbh.c`,
`hid_host.c`, and `ohci.c`; none are linked into the boot ROM.

Local OHCI changes: select `ohci_litex.h` for the LiteX register/IRQ seam and
bound hardware ownership/reset waits. Reserve a TD immediately when allocating
it, including an empty ED tail. Upstream 0.20.0 returned unreserved dummy tails,
so the three receiver endpoints reused one TD and overwrote each other's input
requests. The monitor's `test usb` now checks the actual periodic ED chain for
TD ownership shared across endpoints; it failed on the old driver on this board.
The platform uses physical DDR addresses,
no data cache, and RISC-V fences at descriptor/payload ownership handoffs.
The FPGA core is separately pinned in requirements.in; generated Gowin source
preserves Spinal BufferCC registers as FFs. This package does not add an RTOS.
