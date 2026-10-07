// SPDX-License-Identifier: GPL-2.0+
/*
 * LiteEth EtherMAC (LiteX) + RMII PHY driver for the riscv-mini TangPrimer 20K
 * SoC. This is the same core the BIOS HAL drives (firmware/hal/src/ethernet.c):
 * a LiteEth MAC with two RX and two TX packet slots in a private SRAM window,
 * plus a bit-banged clause-22 MDIO to an RTL8201F PHY. The IP is a modified
 * LiteEth, so no mainline U-Boot driver matches it; this mirrors the HAL.
 *
 * Register map (byte offsets, native-endian 32-bit accesses), relative to the
 * ethmac CSR base (reg index 0):
 *   writer: slot 0x00 length 0x04 errors 0x08 ev_status 0x0c ev_pending 0x10
 *           ev_enable 0x14
 *   reader: start 0x18 ready 0x1c level 0x20 slot 0x24 length 0x28
 *           ev_status 0x2c ev_pending 0x30 ev_enable 0x34
 * PHY MDIO block (reg index 1): crg_reset 0x00 mdio_w 0x04 mdio_r 0x08
 *   mdio_w bits: 0 MDC, 1 OE, 2 W ; mdio_r bit 0 = R
 * Packet slots (reg index 2 = RX, reg index 3 = TX): 2 slots x 0x800 bytes,
 *   frame bytes packed little-endian into 32-bit words.
 *
 * The RX event-pending bit releases the slot; like the LiteUART RX path it must
 * be written back as 1 (write-to-clear) after the frame is copied, otherwise the
 * slot is never handed back.
 */
#include <dm.h>
#include <dm/device_compat.h>
#include <log.h>
#include <mapmem.h>
#include <net.h>
#include <asm/io.h>
#include <linux/delay.h>
#include <linux/err.h>
#include <linux/ioport.h>
#include <linux/types.h>

/* ethmac CSR offsets */
#define WR_SLOT		0x00
#define WR_LEN		0x04
#define WR_ERR		0x08
#define WR_EV_PENDING	0x10
#define WR_EV_ENABLE	0x14
#define RD_START	0x18
#define RD_READY	0x1c
#define RD_SLOT		0x24
#define RD_LEN		0x28
#define RD_EV_PENDING	0x30
#define RD_EV_ENABLE	0x34

/* PHY MDIO CSR offsets */
#define PHY_CRG_RESET	0x00
#define PHY_MDIO_W	0x04
#define PHY_MDIO_R	0x08
#define MDIO_MDC	0
#define MDIO_OE		1
#define MDIO_W		2

#define ETH_MAX_FRAME	1518
#define SLOT_SIZE	0x800
#define RX_SLOTS	2
#define TX_SLOTS	2

/* RTL8201F identification (clause-22 PHYID1/PHYID2) */
#define PHY_ID1		0x001c
#define PHY_ID2_MASK	0xfff0
#define PHY_ID2_VAL	0xc810

struct liteeth_priv {
	void __iomem *mac;	/* ethmac CSR base */
	void __iomem *phy;	/* ethphy MDIO CSR base */
	void __iomem *rx_base;	/* RX packet slots */
	void __iomem *tx_base;	/* TX packet slots */
	int phy_addr;
	int tx_slot;
	int holding;		/* a received frame is buffered, not yet freed */
	int rx_len;
	uchar rx_buf[ETH_MAX_FRAME];
};

/* ---- bit-banged clause-22 MDIO (mirrors the BIOS HAL) ---- */
static void mdio_half_cycle(void)
{
	udelay(1);
}

static int mdio_bit(void __iomem *phy, int output, int value)
{
	int pins = (output ? 2u : 0u) | (value ? 4u : 0u);

	out_le32(phy + PHY_MDIO_W, pins);
	mdio_half_cycle();
	/* Sample the bit established while MDC is low; RTL8201F advances MDIO
	 * after the rising edge, so sampling after that edge reads the NEXT bit.
	 */
	int received = in_le32(phy + PHY_MDIO_R) & 1u;
	out_le32(phy + PHY_MDIO_W, pins | 1u);
	mdio_half_cycle();
	out_le32(phy + PHY_MDIO_W, pins);
	return received;
}

static void mdio_bits(void __iomem *phy, unsigned value, unsigned count)
{
	while (count)
		mdio_bit(phy, 1, (value >> (--count)) & 1u);
}

static void mdio_header(void __iomem *phy, unsigned addr, unsigned reg, int read)
{
	mdio_bits(phy, ~0u, 32);
	mdio_bits(phy, 1, 2);
	mdio_bits(phy, read ? 2 : 1, 2);
	mdio_bits(phy, addr, 5);
	mdio_bits(phy, reg, 5);
}

static int mdio_read(void __iomem *phy, unsigned addr, unsigned reg, u16 *value)
{
	unsigned i, data = 0;

	if (addr > 31 || reg > 31)
		return -EINVAL;
	mdio_header(phy, addr, reg, 1);
	mdio_bit(phy, 0, 1);
	int ack = mdio_bit(phy, 0, 1);

	for (i = 0; i < 16; i++)
		data = (data << 1) | mdio_bit(phy, 0, 1);
	mdio_bit(phy, 0, 1);
	out_le32(phy + PHY_MDIO_W, 0);
	*value = data;
	return ack ? -EIO : 0;
}

static void mdio_write(void __iomem *phy, unsigned addr, unsigned reg, u16 value)
{
	if (addr > 31 || reg > 31)
		return;
	mdio_header(phy, addr, reg, 0);
	mdio_bits(phy, 2, 2);
	mdio_bits(phy, value, 16);
	mdio_bit(phy, 0, 1);
	out_le32(phy + PHY_MDIO_W, 0);
}

/* ---- PHY bring-up (RTL8201F, 100M full duplex only) ---- */
static int liteeth_phy_init(struct liteeth_priv *priv)
{
	void __iomem *phy = priv->phy;
	u16 id1 = 0, id2 = 0, v;
	unsigned addr, scan;

	out_le32(phy + PHY_CRG_RESET, 1);
	udelay(10);
	out_le32(phy + PHY_CRG_RESET, 0);
	udelay(10);

	/* Address 0 can be the PHY broadcast alias: prefer individual 1..31. */
	for (scan = 0; scan < 32; scan++) {
		addr = (scan + 1) & 31u;
		if (mdio_read(phy, addr, 2, &id1) == 0 &&
		    mdio_read(phy, addr, 3, &id2) == 0 &&
		    id1 == PHY_ID1 && (id2 & PHY_ID2_MASK) == PHY_ID2_VAL)
			break;
	}
	if (scan == 32) {
		log_debug("liteeth: no RTL8201F on MDIO\n");
		return -ENODEV;
	}
	priv->phy_addr = addr;

	mdio_write(phy, addr, 31, 0);
	mdio_write(phy, addr, 0, 0x8000);	/* soft reset */
	if (mdio_read(phy, addr, 0, &v))
		return -EIO;
	/* Wait for the reset bit to self-clear. */
	for (int i = 0; i < 500; i++) {
		if (mdio_read(phy, addr, 0, &v))
			return -EIO;
		if (!(v & 0x8000))
			break;
		mdelay(1);
	}
	if (v & 0x8000)
		return -ETIMEDOUT;

	/* Preserve vendor RMII timing offsets: REF_CLK output, CRS_DV normal,
	 * no SSD error; disable clock-stopping power saving / EEE.
	 */
	mdio_write(phy, addr, 31, 7);
	if (mdio_read(phy, addr, 16, &v))
		return -EIO;
	mdio_write(phy, addr, 16, (v | 8u) & ~0x1006u);
	mdio_write(phy, addr, 24, 1);		/* disable REF_CLK spread spectrum */
	mdio_write(phy, addr, 31, 0);
	if (mdio_read(phy, addr, 24, &v))
		return -EIO;
	mdio_write(phy, addr, 24, v & ~0x8000u);
	/* MMD7 EEE advertisement off. */
	mdio_write(phy, addr, 13, 7);
	mdio_write(phy, addr, 14, 60);
	mdio_write(phy, addr, 13, 0x4007);
	mdio_write(phy, addr, 14, 0);
	/* Advertise only 100M full duplex, then restart autonegotiation. */
	mdio_write(phy, addr, 4, 0x0101);
	mdio_write(phy, addr, 0, 0x1200);
	return 0;
}

static int liteeth_link_up(struct liteeth_priv *priv)
{
	u16 status = 0, control = 0, partner = 0;

	if (mdio_read(priv->phy, priv->phy_addr, 1, &status))
		return 0;
	if (mdio_read(priv->phy, priv->phy_addr, 1, &status))
		return 0;
	if (mdio_read(priv->phy, priv->phy_addr, 0, &control))
		return 0;
	if (mdio_read(priv->phy, priv->phy_addr, 5, &partner))
		return 0;
	return (status & 0x24u) == 0x24u && (control & 0x2100u) == 0x2100u &&
	       (partner & 0x100u);
}

/* ---- DM eth ops ---- */
static int liteeth_start(struct udevice *dev)
{
	struct liteeth_priv *priv = dev_get_priv(dev);
	int ret;

	ret = liteeth_phy_init(priv);
	if (ret)
		return ret;

	/* Wait for link (autoneg to 100M FD). */
	for (int i = 0; i < 3000; i++) {
		if (liteeth_link_up(priv))
			break;
		mdelay(1);
	}
	if (!liteeth_link_up(priv)) {
		log_debug("liteeth: link down\n");
		return -ENOLINK;
	}

	/* Arm the RX slot-released event and the TX-complete event. */
	out_le32(priv->mac + WR_EV_PENDING, 1);
	out_le32(priv->mac + WR_EV_ENABLE, 1);
	out_le32(priv->mac + RD_EV_PENDING, 1);
	out_le32(priv->mac + RD_EV_ENABLE, 1);
	priv->tx_slot = 0;
	priv->holding = 0;
	return 0;
}

static void liteeth_stop(struct udevice *dev)
{
	struct liteeth_priv *priv = dev_get_priv(dev);

	out_le32(priv->mac + WR_EV_ENABLE, 0);
	out_le32(priv->mac + RD_EV_ENABLE, 0);
}

static int liteeth_send(struct udevice *dev, void *packet, int length)
{
	struct liteeth_priv *priv = dev_get_priv(dev);
	volatile u32 *slot;
	const u8 *bytes = packet;
	int i;

	if (length < 14 || length > ETH_MAX_FRAME)
		return -EINVAL;
	if (!in_le32(priv->mac + RD_READY))
		return -EBUSY;

	slot = (volatile u32 *)(priv->tx_base + priv->tx_slot * SLOT_SIZE);
	for (i = 0; i < length; i += 4) {
		u32 word = 0;

		for (int b = 0; b < 4 && i + b < length; b++)
			word |= (unsigned)bytes[i + b] << (8 * b);
		slot[i / 4] = word;
	}

	out_le32(priv->mac + RD_SLOT, priv->tx_slot);
	out_le32(priv->mac + RD_LEN, length);
	wmb();
	out_le32(priv->mac + RD_START, 1);
	priv->tx_slot ^= 1;

	/* Wait for the MAC to finish draining the slot (TX-complete event). */
	for (i = 0; i < 1000; i++) {
		if (in_le32(priv->mac + RD_EV_PENDING) & 1u) {
			out_le32(priv->mac + RD_EV_PENDING, 1);
			return 0;
		}
		udelay(10);
	}
	return -ETIMEDOUT;
}

static int liteeth_recv(struct udevice *dev, int flags, uchar **packetp)
{
	struct liteeth_priv *priv = dev_get_priv(dev);
	volatile const u32 *slot;
	uchar *bytes = priv->rx_buf;
	unsigned size, index;

	if (priv->holding)
		return -EAGAIN;
	if (!(in_le32(priv->mac + WR_EV_PENDING) & 1u))
		return -EAGAIN;

	/* Freeze the writer so the slot is not overwritten while we copy it
	 * (mirrors the BIOS HAL disabling sram_writer ev_enable on pending).
	 */
	out_le32(priv->mac + WR_EV_ENABLE, 0);
	size = in_le32(priv->mac + WR_LEN);
	index = in_le32(priv->mac + WR_SLOT);
	if (size < 14 || size > ETH_MAX_FRAME || index >= RX_SLOTS) {
		/* Drop the bad frame but hand the slot back. */
		out_le32(priv->mac + WR_EV_PENDING, 1);
		out_le32(priv->mac + WR_EV_ENABLE, 1);
		return -EAGAIN;
	}

	slot = (volatile const u32 *)(priv->rx_base + index * SLOT_SIZE);
	for (unsigned i = 0; i < size; i += 4) {
		u32 word = slot[i / 4];

		for (int b = 0; b < 4 && i + b < size; b++)
			bytes[i + b] = word >> (8 * b);
	}
	priv->rx_len = size;
	priv->holding = 1;
	*packetp = priv->rx_buf;
	return size;
}

static int liteeth_free_pkt(struct udevice *dev, uchar *packet, int length)
{
	struct liteeth_priv *priv = dev_get_priv(dev);

	/* Release the RX slot: write-to-clear the event-pending bit, then
	 * re-arm so the next frame is captured.
	 */
	out_le32(priv->mac + WR_EV_PENDING, 1);
	out_le32(priv->mac + WR_EV_ENABLE, 1);
	priv->holding = 0;
	return 0;
}

static const struct eth_ops liteeth_ops = {
	.start		= liteeth_start,
	.send		= liteeth_send,
	.recv		= liteeth_recv,
	.free_pkt	= liteeth_free_pkt,
	.stop		= liteeth_stop,
};

static int liteeth_of_to_plat(struct udevice *dev)
{
	struct liteeth_priv *priv = dev_get_priv(dev);
	struct resource res;
	int i;
	void __iomem *maps[4];

	for (i = 0; i < 4; i++) {
		if (dev_read_resource(dev, i, &res))
			return -EINVAL;
		maps[i] = map_sysmem(res.start, 0);
	}
	priv->mac = maps[0];
	priv->phy = maps[1];
	priv->rx_base = maps[2];
	priv->tx_base = maps[3];
	return 0;
}

static const struct udevice_id liteeth_ids[] = {
	{ .compatible = "riscv-mini,liteeth" },
	{ }
};

U_BOOT_DRIVER(liteeth) = {
	.name		= "liteeth",
	.id		= UCLASS_ETH,
	.of_match	= liteeth_ids,
	.of_to_plat	= liteeth_of_to_plat,
	.ops		= &liteeth_ops,
	.priv_auto	= sizeof(struct liteeth_priv),
	.plat_auto	= sizeof(struct eth_pdata),
};
