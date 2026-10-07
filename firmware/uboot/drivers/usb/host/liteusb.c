// SPDX-License-Identifier: GPL-2.0+
/*
 * riscv-mini custom Ultraembedded PIO full-speed USB host driver for U-Boot.
 *
 * The host is a bit-level PIO engine (gateware/usb_ultra.py): the CPU launches
 * one USB packet at a time via TOKEN and reads the device response from RX_STAT.
 * There is no DMA and no frame scheduler; every transaction is polled. The
 * controller exposes a single full-speed root port, so this driver emulates the
 * root hub for U-Boot's USB stack (port status/reset + root-hub descriptors)
 * and implements the SETUP/DATA/STATUS control engine plus bulk/interrupt
 * transfers with per-endpoint data-toggle tracking.
 *
 * Register map (32-bit, from firmware/hal/src/hcd_ultra.c):
 *   CTRL 0 STATUS 4 IRQ_ACK 8 IRQ_STS 12 IRQ_MASK 16
 *   TX_LEN 20 TOKEN 24 RX_STAT 28 FIFO 32
 */

#include <dm.h>
#include <dm/device_compat.h>
#include <log.h>
#include <mapmem.h>
#include <usb.h>
#include <usb_defs.h>
#include <linux/usb/ch9.h>
#include <asm/io.h>
#include <linux/delay.h>
#include <linux/err.h>
#include <linux/types.h>
#include <linux/ioport.h>
#include <usbroothubdes.h>
#include <string.h>

#define MAX_EP_TRACK	32
#define PKT_GUARD_US	1000	/* avoid reading a stale SIE-idle (see hcd_ultra) */
#define PKT_TIMEOUT_MS	50
#define NAK_RETRIES	300

enum { R_CTRL = 0, R_STATUS = 4, R_IRQ_ACK = 8, R_IRQ_STS = 12, R_IRQ_MASK = 16,
       R_TX_LEN = 20, R_TOKEN = 24, R_RX_STAT = 28, R_FIFO = 32 };

enum { FS_CTRL = 0xe9, RESET_CTRL = 0xc4, RUN_CTRL = 0xe9 | 256,
       IRQ_DONE = 2, IRQ_ERR = 4,
       PID_SETUP = 0x2d, PID_OUT = 0xe1, PID_IN = 0x69,
       PID_ACK = 0xd2, PID_NAK = 0x5a, PID_STALL = 0x1e,
       PID_DATA0 = 0xc3, PID_DATA1 = 0x4b };

struct liteusb_plat {
	void __iomem *regs;	/* PIO transaction block (0xb1000000) */
	void __iomem *ctrl;	/* usb_host enable/reset/ready CSR block (0xf000a800) */
	void __iomem *phyreset;	/* shared PHY reset (F10, 0xf0006800); resets BOTH
				 * the USB and Ethernet external PHYs (hal.h:54).
				 * Active-low: 0 = assert, 1 = release. NULL if the
				 * DT omits it. */
};

struct ep_toggle {
	u8 addr;	/* device address */
	u8 ep;		/* endpoint address incl. 0x80 direction bit */
	u8 toggle;
	bool used;
};

struct liteusb_priv {
	struct ep_toggle eps[MAX_EP_TRACK];
	int root_hub_devnum;	/* device address the core assigns to the root hub */
	u16 port_change;
	bool port_enabled;
	u8 status_buf[8];
};

/* Root hub with a single port (the header's descriptor advertises two). */
static u8 liteusb_hub_des[] = {
	0x09, 0x29, 0x01, 0x00, 0x00, 0x01, 0x00, 0x00, 0xff,
};

static struct ep_toggle *toggle_get(struct liteusb_priv *priv, u8 addr, u8 ep)
{
	struct ep_toggle *free = NULL;

	for (int i = 0; i < MAX_EP_TRACK; i++) {
		struct ep_toggle *e = &priv->eps[i];
		if (e->used && e->addr == addr && e->ep == ep)
			return e;
		if (!e->used && !free)
			free = e;
	}
	if (!free)
		return NULL;
	free->used = true;
	free->addr = addr;
	free->ep = ep;
	free->toggle = 0;
	return free;
}

/*
 * Launch one packet and wait for the device response.
 * in=1: IN token, data read into rbuf, bounded by cap bytes. in=0: OUT token,
 * data from data. Returns 0 on a completed transaction; *rpid is the device PID
 * (ACK/NAK/STALL/DATA0/DATA1), *rcount the byte count the device actually sent
 * (may exceed cap — the FIFO is always fully drained). Callers must advance
 * their transfer offset by min(*rcount, cap), not by *rcount, and use *rcount
 * only for short-packet detection. Negative on a host-side error.
 */
static int liteusb_packet(struct liteusb_plat *plat, int addr, int ep, int in,
			  int toggle, int pid, const u8 *data, int len,
			  u8 *rbuf, int cap, int *rcount, int *rpid)
{
	void __iomem *regs = plat->regs;
	u32 status = 0;
	unsigned long deadline;
	int count, rpidv;

	/* The core flags a host-side error (CRC/timeout) via RX_STAT bits 29/30
	 * or an over-long count. hcd_ultra retries such a packet up to 3 times
	 * before failing; mirror that — the first packet right after a device
	 * address change often needs one retry. */
	for (int err_try = 0; err_try < 3; err_try++) {
		writel(IRQ_DONE | IRQ_ERR, regs + R_IRQ_ACK);
		writel(RUN_CTRL, regs + R_CTRL);

		if (in) {
			writel(0, regs + R_TX_LEN);
		} else {
			for (int i = 0; i < len; i++)
				writeb(data[i], regs + R_FIFO);
			writel(len, regs + R_TX_LEN);
		}

		writel((1u << 31) | (1u << 29) | (in ? (1u << 30) : 0) |
		       ((u32)toggle << 28) | ((u32)pid << 16) |
		       ((u32)addr << 9) | ((u32)(ep & 15) << 5), regs + R_TOKEN);

		/* The SIE reports idle one ms after launch; guard against a stale read. */
		udelay(PKT_GUARD_US);
		deadline = get_timer(0) + PKT_TIMEOUT_MS;
		for (;;) {
			status = readl(regs + R_RX_STAT);
			/* bit31 = start-pending (transaction still running),
			 * bit28 = SIE idle. Not done while either indicates busy. */
			if (!(status & (1u << 31)) && (status & (1u << 28)))
				break;
			if (get_timer(0) > deadline)
				return -ETIMEDOUT;
		}

		writel(IRQ_DONE | IRQ_ERR, regs + R_IRQ_ACK);
		count = status & 0xffff;
		rpidv = (status >> 16) & 0xff;
		if (count > 64 || (status & ((1u << 30) | (1u << 29))))
			continue;	/* host-side error: retry the packet */

		if (in && rbuf) {
			int n = count < cap ? count : cap;
			for (int i = 0; i < n; i++)
				rbuf[i] = readb(regs + R_FIFO);
			/* Drain bytes beyond the caller's buffer so the FIFO is
			 * empty for the next transaction. */
			for (int i = n; i < count; i++)
				(void)readb(regs + R_FIFO);
		} else {
			for (int i = 0; i < count; i++)
				(void)readb(regs + R_FIFO);
		}

		if (rcount)
			*rcount = count;
		if (rpid)
			*rpid = rpidv;
		return 0;
	}

	return -EPROTO;
}

/* Full control transfer: SETUP + optional DATA + STATUS, per USB 2.0 8.5.3. */
static int liteusb_control_xfer(struct liteusb_plat *plat,
				struct liteusb_priv *priv, struct usb_device *dev,
				unsigned long pipe, void *buffer, int len,
				struct devrequest *setup)
{
	int addr = usb_pipedevice(pipe);
	int ep = usb_pipeendpoint(pipe);
	int in = usb_pipein(pipe);
	int rpid, cnt, ret, tries, toggle, act = 0;
	int mps = usb_maxpacket(dev, pipe);
	u8 status_buf[8];

	if (mps < 8)
		mps = 8;

	/* SETUP stage (always OUT, PID_SETUP). */
	for (tries = 0; ; tries++) {
		ret = liteusb_packet(plat, addr, ep, 0, 0, PID_SETUP,
				     (const u8 *)setup, 8, NULL, 0, &cnt, &rpid);
		if (ret)
			return ret;
		if (rpid == PID_ACK)
			break;
		if (rpid == PID_STALL)
			return -EPIPE;
		if (tries >= NAK_RETRIES)
			return -EIO;
	}

	/* DATA stage: starts at DATA1 and toggles per packet. */
	toggle = 1;
	if (len > 0 && buffer) {
		int nak = 0;
		while (act < len) {
			int thislen = len - act;
			if (thislen > mps)
				thislen = mps;

			if (in) {
				int cap = len - act;

				ret = liteusb_packet(plat, addr, ep, 1, toggle,
						      PID_IN, NULL, 0,
						      (u8 *)buffer + act,
						      cap, &cnt, &rpid);
				if (ret)
					return ret;
				if (rpid == PID_STALL)
					return -EPIPE;
				if (rpid == PID_NAK) {
					if (++nak >= NAK_RETRIES)
						return -EIO;
					continue;
				}
				if (rpid != PID_DATA0 && rpid != PID_DATA1)
					return -EPROTO;
				/* cnt is what the device sent; only min(cnt, cap)
				 * actually landed in the buffer. Advance by that,
				 * but still use cnt for short-packet detection. */
				act += cnt < cap ? cnt : cap;
				toggle ^= 1;
				nak = 0;
				if (cnt < mps)
					break;
			} else {
				ret = liteusb_packet(plat, addr, ep, 0, toggle,
						      PID_OUT,
						      (const u8 *)buffer + act,
						      thislen, NULL, 0, &cnt, &rpid);
				if (ret)
					return ret;
				if (rpid == PID_STALL)
					return -EPIPE;
				if (rpid != PID_ACK) {
					if (++nak >= NAK_RETRIES)
						return -EIO;
					continue;
				}
				act += thislen;
				toggle ^= 1;
				nak = 0;
			}
		}
	}

	/* STATUS stage: opposite direction, always DATA1, zero length.
	 * A no-data control transfer ends with an IN status. */
	{
		int sdir = (len > 0 && buffer) ? !in : 1;
		for (tries = 0; ; tries++) {
			if (sdir)
				ret = liteusb_packet(plat, addr, ep, 1, 1, PID_IN,
						      NULL, 0, status_buf,
						      sizeof(status_buf), &cnt, &rpid);
			else
				ret = liteusb_packet(plat, addr, ep, 0, 1, PID_OUT,
						      status_buf, 0, NULL, 0,
						      &cnt, &rpid);
			if (ret)
				return ret;
			if (rpid == PID_ACK || rpid == PID_DATA0 || rpid == PID_DATA1)
				break;
			if (rpid == PID_STALL)
				return -EPIPE;
			if (tries >= NAK_RETRIES)
				return -EIO;
		}
	}

	dev->act_len = act;
	dev->status = 0;
	return 0;
}

/* Power up / re-init the USB host PHY via the usb_host control CSRs, exactly as
 * the BIOS hal_usb_init does: enable, hold reset, wait for ready, verify PHY
 * id, then deassert reset. Without this the PIO block stays in reset and every
 * read returns 0xffffffff. Re-running it forces the device to re-attach from a
 * clean PHY state (the device suspends once SOF stops, e.g. across the BIOS ->
 * U-Boot handoff, and a soft-PHY SE0 alone does not always wake it). */
static int liteusb_phy_init(struct liteusb_plat *plat)
{
	void __iomem *regs = plat->regs;
	void __iomem *ctrl = plat->ctrl;
	unsigned long deadline;

	/* Hard-reset the external PHY first. F10 (0xf0006800) is shared with
	 * the Ethernet PHY (hal.h:54), so this also drops the ETH link — but
	 * it is the only way to bring a device that suspended during the
	 * BIOS->U-Boot gap back to default (address 0): the CSR core reset
	 * alone never disturbs the external PHY or the bus. */
	if (plat->phyreset) {
		writel(0, plat->phyreset);	/* assert (active-low) */
		mdelay(20);
		writel(1, plat->phyreset);	/* release */
		mdelay(50);
	}

	writel(1, ctrl + 0x00);			/* usb_host_enable */
	writel(1, ctrl + 0x04);			/* usb_host_reset (held) */
	deadline = get_timer(0) + 200;
	while (!readl(ctrl + 0x08)) {		/* usb_host_ready */
		if (readl(ctrl + 0x0c) || get_timer(0) > deadline) {	/* error */
			printf("liteusb: PHY not ready (err=%x)\n", readl(ctrl + 0x0c));
			return -ENODEV;
		}
	}
	if (readl(ctrl + 0x10) != 0x00060424u) {	/* usb_host_id */
		printf("liteusb: bad PHY id %x\n", readl(ctrl + 0x10));
		return -ENODEV;
	}
	writel(0, ctrl + 0x04);			/* usb_host_reset deassert */
	udelay(2000);

	writel(0, regs + R_IRQ_MASK);
	writel(15, regs + R_IRQ_ACK);
	writel((FS_CTRL & ~1u) | 256, regs + R_CTRL);
	return 0;
}

/* Root-hub emulation: port status/reset + root-hub descriptors. */
static int liteusb_rh_msg(struct liteusb_plat *plat, struct liteusb_priv *priv,
			  struct usb_device *dev, unsigned long pipe,
			  void *buffer, int len, struct devrequest *setup)
{
	void __iomem *regs = plat->regs;
	u16 wValue = le16_to_cpu(setup->value);
	u16 wIndex = le16_to_cpu(setup->index);
	u16 wLength = le16_to_cpu(setup->length);
	int outlen = 0, port = wIndex;

	switch (setup->requesttype & ~USB_DIR_IN) {
	case 0: /* standard requests to the root-hub device */
		switch (setup->request) {
		case USB_REQ_GET_DESCRIPTOR:
			switch (wValue >> 8) {
			case USB_DT_DEVICE:
				outlen = min3(len, (int)sizeof(root_hub_dev_des), (int)wLength);
				memcpy(buffer, root_hub_dev_des, outlen);
				break;
			case USB_DT_CONFIG:
				outlen = min3(len, (int)sizeof(root_hub_config_des), (int)wLength);
				memcpy(buffer, root_hub_config_des, outlen);
				break;
			case USB_DT_STRING:
				if ((wValue & 0xff) == 0) {
					outlen = min3(len, (int)sizeof(root_hub_str_index0), (int)wLength);
					memcpy(buffer, root_hub_str_index0, outlen);
				} else {
					outlen = min3(len, (int)sizeof(root_hub_str_index1), (int)wLength);
					memcpy(buffer, root_hub_str_index1, outlen);
				}
				break;
			default:
				return -EOPNOTSUPP;
			}
			break;
		case USB_REQ_SET_ADDRESS:
			/* Only the root hub reaches this case (the real device's
			 * SET_ADDRESS is sent on the wire once root_hub_devnum
			 * is non-zero). Record the address the core assigns us.
			 */
			priv->root_hub_devnum = wValue;
			outlen = 0;
			break;
		case USB_REQ_SET_CONFIGURATION:
			outlen = 0;
			break;
		case USB_REQ_GET_STATUS:
			if (len >= 2) {
				*(__le16 *)buffer = cpu_to_le16(1); /* self-powered */
				outlen = 2;
			}
			break;
		default:
			return -EOPNOTSUPP;
		}
		break;

	case USB_RT_HUB:
		if (setup->request == USB_REQ_GET_DESCRIPTOR &&
		    (wValue >> 8) == USB_DT_HUB) {
			outlen = min3(len, (int)sizeof(liteusb_hub_des), (int)wLength);
			memcpy(buffer, liteusb_hub_des, outlen);
		} else if (setup->request == USB_REQ_GET_STATUS) {
			if (len >= 4) {
				memset(buffer, 0, 4);
				outlen = 4;
			}
		} else {
			/* SET_HUB_DEPTH and other hub requests: accept as no-ops. */
			outlen = 0;
		}
		break;

	case USB_RT_PORT:
		if (port != 1)
			return -EINVAL;
		if (setup->request == USB_REQ_GET_STATUS) {
			u32 ps = USB_PORT_STAT_POWER;
			u32 hw = readl(regs + R_STATUS);
			if ((hw & 3) == 1)
				ps |= USB_PORT_STAT_CONNECTION;
			if (priv->port_enabled)
				ps |= USB_PORT_STAT_ENABLE;
			if (len >= 4) {
				*(__le32 *)buffer = cpu_to_le32(ps |
					((u32)priv->port_change << 16));
				outlen = 4;
			}
		} else if (setup->request == USB_REQ_SET_FEATURE) {
			if (le16_to_cpu(setup->value) == USB_PORT_FEAT_RESET) {
				writel(RESET_CTRL, regs + R_CTRL);
				mdelay(20);
				writel(RUN_CTRL, regs + R_CTRL);
				priv->port_enabled = true;
				priv->port_change |= USB_PORT_STAT_C_RESET;
			}
		} else if (setup->request == USB_REQ_CLEAR_FEATURE) {
			u16 feat = le16_to_cpu(setup->value);
			if (feat == USB_PORT_FEAT_C_RESET)
				priv->port_change &= ~USB_PORT_STAT_C_RESET;
			else if (feat == USB_PORT_FEAT_C_CONNECTION)
				priv->port_change &= ~USB_PORT_STAT_C_CONNECTION;
			else if (feat == USB_PORT_FEAT_ENABLE)
				priv->port_enabled = false;
		} else {
			return -EOPNOTSUPP;
		}
		break;

	default:
		return -EOPNOTSUPP;
	}

	dev->act_len = outlen;
	dev->status = 0;
	return 0;
}

static int liteusb_control(struct udevice *bus, struct usb_device *dev,
			   unsigned long pipe, void *buffer, int len,
			   struct devrequest *setup)
{
	struct liteusb_plat *plat = dev_get_plat(bus);
	struct liteusb_priv *priv = dev_get_priv(bus);
	bool rh = usb_pipedevice(pipe) == priv->root_hub_devnum;

	if (rh)
		return liteusb_rh_msg(plat, priv, dev, pipe, buffer, len, setup);

	return liteusb_control_xfer(plat, priv, dev, pipe, buffer, len, setup);
}

/* Bulk / interrupt transfer with persistent per-endpoint data toggle. */
static int liteusb_xfer(struct udevice *bus, struct usb_device *dev,
			unsigned long pipe, void *buffer, int length, int in,
			bool interrupt)
{
	struct liteusb_plat *plat = dev_get_plat(bus);
	struct liteusb_priv *priv = dev_get_priv(bus);
	int addr = usb_pipedevice(pipe);
	int epnum = usb_pipeendpoint(pipe);
	int epaddr = epnum | (in ? 0x80 : 0);
	int mps = usb_maxpacket(dev, pipe);
	struct ep_toggle *tg;
	int rpid, cnt, ret, act = 0, nak = 0;

	if (mps < 1)
		mps = interrupt ? 8 : 64;

	tg = toggle_get(priv, addr, epaddr);
	if (!tg)
		return -ENOMEM;

	if (in) {
		while (act < length) {
			int want = length - act;
			if (want > mps)
				want = mps;
			ret = liteusb_packet(plat, addr, epnum, 1, tg->toggle,
					      PID_IN, NULL, 0,
					      (u8 *)buffer + act, want, &cnt, &rpid);
			if (ret)
				return ret;
			if (rpid == PID_STALL)
				return -EPIPE;
			if (rpid == PID_NAK) {
				if (interrupt || ++nak >= NAK_RETRIES)
					return -EAGAIN;
				continue;
			}
			if (rpid != PID_DATA0 && rpid != PID_DATA1)
				return -EPROTO;
			tg->toggle ^= 1;
			/* cnt is what the device sent; only min(cnt, want) landed
			 * in the buffer. Advance by that, keep cnt for the
			 * short-packet test. */
			act += cnt < want ? cnt : want;
			nak = 0;
			if (cnt < mps)
				break;
			if (interrupt)
				break;
		}
	} else {
		while (act < length) {
			int thislen = length - act;
			if (thislen > mps)
				thislen = mps;
			ret = liteusb_packet(plat, addr, epnum, 0, tg->toggle,
					      PID_OUT,
					      (const u8 *)buffer + act, thislen,
					      NULL, 0, &cnt, &rpid);
			if (ret)
				return ret;
			if (rpid == PID_STALL)
				return -EPIPE;
			if (rpid != PID_ACK) {
				if (++nak >= NAK_RETRIES)
					return -EAGAIN;
				continue;
			}
			tg->toggle ^= 1;
			act += thislen;
			nak = 0;
		}
	}

	dev->act_len = act;
	dev->status = 0;
	return 0;
}

static int liteusb_bulk(struct udevice *bus, struct usb_device *dev,
			unsigned long pipe, void *buffer, int length)
{
	if (usb_hub_is_root_hub(dev->dev))
		return -EINVAL;
	return liteusb_xfer(bus, dev, pipe, buffer, length, usb_pipein(pipe),
			    false);
}

static int liteusb_interrupt(struct udevice *bus, struct usb_device *dev,
			     unsigned long pipe, void *buffer, int length,
			     int interval, bool nonblock)
{
	if (usb_hub_is_root_hub(dev->dev))
		return -EINVAL;
	return liteusb_xfer(bus, dev, pipe, buffer, length, usb_pipein(pipe),
			    true);
}

static int liteusb_get_max_xfer_size(struct udevice *bus, size_t *size)
{
	*size = 65535;
	return 0;
}

static int liteusb_reset_root_port(struct udevice *bus, struct usb_device *udev)
{
	struct liteusb_plat *plat = dev_get_plat(bus);
	struct liteusb_priv *priv = dev_get_priv(bus);
	void __iomem *regs = plat->regs;

	writel(RESET_CTRL, regs + R_CTRL);
	mdelay(20);
	writel(RUN_CTRL, regs + R_CTRL);
	priv->port_enabled = false;
	priv->port_change = 0;
	return 0;
}

static int liteusb_probe(struct udevice *bus)
{
	struct liteusb_plat *plat = dev_get_plat(bus);
	int ret;

	/* BIOS stops the PHY (enable=0, reset=1) before jumping to U-Boot, so
	 * the PIO block reads 0xffffffff until we bring it back up. */
	ret = liteusb_phy_init(plat);
	if (ret)
		return ret;

	return 0;
}

static int liteusb_of_to_plat(struct udevice *bus)
{
	struct liteusb_plat *plat = dev_get_plat(bus);
	struct resource res0, res1, res2;

	if (dev_read_resource(bus, 0, &res0))
		return -EINVAL;
	if (dev_read_resource(bus, 1, &res1))
		return -EINVAL;

	plat->regs = (void __iomem *)(uintptr_t)res0.start;
	plat->ctrl = (void __iomem *)(uintptr_t)res1.start;
	/* Optional: shared PHY reset. Without it we cannot hard-reset the
	 * external PHY, so a device left suspended across the BIOS->U-Boot
	 * gap cannot be brought back to default state. */
	plat->phyreset = dev_read_resource(bus, 2, &res2) ? NULL :
		(void __iomem *)(uintptr_t)res2.start;
	return 0;
}

static const struct dm_usb_ops liteusb_usb_ops = {
	.control = liteusb_control,
	.bulk = liteusb_bulk,
	.interrupt = liteusb_interrupt,
	.get_max_xfer_size = liteusb_get_max_xfer_size,
	.reset_root_port = liteusb_reset_root_port,
};

static const struct udevice_id liteusb_ids[] = {
	{ .compatible = "riscv-mini,liteusb" },
	{ }
};

U_BOOT_DRIVER(liteusb) = {
	.name = "liteusb",
	.id = UCLASS_USB,
	.of_match = liteusb_ids,
	.of_to_plat = liteusb_of_to_plat,
	.probe = liteusb_probe,
	.priv_auto = sizeof(struct liteusb_priv),
	.plat_auto = sizeof(struct liteusb_plat),
	.ops = &liteusb_usb_ops,
};