// SPDX-License-Identifier: GPL-2.0+
/*
 * LiteX CSR LiteUART with 32-bit-wide registers (this SoC's variant;
 * the mainline "litex,uart0" driver targets the 8-bit register layout).
 * Register map (byte offsets, native-endian 32-bit accesses):
 *   0x00 rxtx      0x04 txfull     0x08 rxempty
 *   0x0c ev_status 0x10 ev_pending 0x14 ev_enable 0x18 txempty 0x1c rxfull
 */
#include <debug_uart.h>
#include <dm.h>
#include <mapmem.h>
#include <serial.h>
#include <asm/io.h>
#include <linux/err.h>
#include <linux/types.h>

#define LITEUART32_RXTX		0x00
#define LITEUART32_TXFULL	0x04
#define LITEUART32_RXEMPTY	0x08
#define LITEUART32_EV_PENDING	0x10
/* RX event-pending bit (offset 1). The LiteUART core only advances its RX
 * FIFO read pointer / deasserts rxempty once this bit is written back as 1
 * (write-to-clear). The BIOS and OpenSBI platform drivers do the same
 * (uart_ev_pending_write(2)) after every uart_rxtx_read(). Without it the
 * first received byte is stuck: rxempty stays 0 and getc re-reads the same
 * byte forever (the observed prompt flood). */
#define LITEUART32_EV_RX	2

struct liteuart32_plat {
	void __iomem *base;
};

static int liteuart32_putc(struct udevice *dev, const char ch)
{
	struct liteuart32_plat *plat = dev_get_plat(dev);

	if (in_le32(plat->base + LITEUART32_TXFULL))
		return -EAGAIN;
	out_le32(plat->base + LITEUART32_RXTX, ch);
	return 0;
}

static int liteuart32_getc(struct udevice *dev)
{
	struct liteuart32_plat *plat = dev_get_plat(dev);

	if (in_le32(plat->base + LITEUART32_RXEMPTY))
		return -EAGAIN;
	{
		int ch = in_le32(plat->base + LITEUART32_RXTX) & 0xff;
		out_le32(plat->base + LITEUART32_EV_PENDING, LITEUART32_EV_RX);
		return ch;
	}
}

static int liteuart32_pending(struct udevice *dev, bool input)
{
	struct liteuart32_plat *plat = dev_get_plat(dev);

	if (input)
		return !in_le32(plat->base + LITEUART32_RXEMPTY);
	return in_le32(plat->base + LITEUART32_TXFULL);
}

static int liteuart32_of_to_plat(struct udevice *dev)
{
	struct liteuart32_plat *plat = dev_get_plat(dev);

	plat->base = dev_read_addr_ptr(dev);
	return IS_ERR(plat->base) ? -EINVAL : 0;
}

static const struct dm_serial_ops liteuart32_ops = {
	.getc = liteuart32_getc,
	.putc = liteuart32_putc,
	.pending = liteuart32_pending,
};

static const struct udevice_id liteuart32_ids[] = {
	{ .compatible = "riscv-mini,liteuart32" },
	{ }
};

U_BOOT_DRIVER(serial_liteuart32) = {
	.name = "serial_liteuart32",
	.id = UCLASS_SERIAL,
	.of_match = liteuart32_ids,
	.of_to_plat = liteuart32_of_to_plat,
	.plat_auto = sizeof(struct liteuart32_plat),
	.ops = &liteuart32_ops,
	.flags = DM_FLAG_PRE_RELOC,
};

/* No DEBUG_UART_FUNCS here: with CONFIG_RISCV_SMODE the debug-UART choice
 * defaults to DEBUG_SBI_CONSOLE (drivers/serial/Kconfig:242), whose
 * implementation lives in serial_sbi.c. Defining these functions in this
 * file as well causes multiple-definition link errors.
 */
