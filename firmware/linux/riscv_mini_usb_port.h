/* SPDX-License-Identifier: GPL-2.0 */
#ifndef RISCV_MINI_USB_PORT_H
#define RISCV_MINI_USB_PORT_H

/* LINESTATE is an instantaneous PHY sample, not a connection latch. */
struct mini_usb_port {
	unsigned int connected, missing;
	unsigned long absent_since;
};

static inline int mini_usb_port_sample(struct mini_usb_port *port,
	unsigned int line, unsigned long now, unsigned long debounce)
{
	unsigned int was_connected = port->connected;
	/* K can occur inside a full-speed packet. Only idle J starts attachment;
	 * a directly attached low-speed device is not supported by this host. */
	if (line == 1 || (port->connected && line == 2)) {
		port->connected = 1;
		port->missing = 0;
	} else if (port->connected) {
		if (!port->missing) {
			port->absent_since = now;
			port->missing = 1;
		} else if ((long)(now - port->absent_since) >= (long)debounce) {
			port->connected = 0;
			port->missing = 0;
		}
	}
	return was_connected != port->connected;
}
#endif
