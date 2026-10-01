# TinyUSB Host subset

Upstream: https://github.com/hathach/tinyusb, release **0.20.0**, commit
`3af1bec1a9161ee8dec29487831f7ac7ade9e189`. MIT license is in LICENSE.

Only common utilities, OS abstraction headers, host headers/core, HID Host and
OHCI are vendored. The application compiles `tusb.c`, `tusb_fifo.c`, `usbh.c`,
`hid_host.c`, and `ohci.c`; none are linked into the boot ROM.

Local OHCI changes: select `ohci_litex.h` for the LiteX register/IRQ seam and
bound hardware ownership/reset waits. The platform uses physical DDR addresses,
no data cache, and RISC-V fences at descriptor/payload ownership handoffs.
The FPGA core is separately pinned in requirements.in; generated Gowin source
preserves Spinal BufferCC registers as FFs. This package does not add an RTOS.
