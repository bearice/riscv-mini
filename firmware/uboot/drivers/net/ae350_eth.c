// SPDX-License-Identifier: GPL-2.0+
/*
 * Ethernet of the riscv-mini AE350 machine: a gigabit RGMII MAC that moves
 * packets through DDR descriptor rings, plus a bit-banged clause-22 MDIO
 * port to the board RTL8211F. Both share one register page:
 *   management: id 0x00, mdio 0x04 (bit0 PHY out of reset, bit1 MDC,
 *               bit2 drive MDIO low), mdio input 0x08
 *   packets:    id 0x10, control 0x80 (1 run, 2 reset), status 0x84,
 *               TX ring 0x88, RX ring 0x8c, ring mask 0x90, TX producer 0x94,
 *               TX done 0x98, RX producer 0x9c, RX consumer 0xa0,
 *               interrupt pending 0xa4, interrupt enable 0xa8, fault 0xac
 * The MAC runs at 1000 Mb/s only, so the PHY advertises 1000BASE-T full
 * duplex alone. The MAC does not filter on the destination address.
 */
#include <cpu_func.h>
#include <dm.h>
#include <dm/device_compat.h>
#include <malloc.h>
#include <net.h>
#include <time.h>
#include <asm/cache.h>
#include <asm/io.h>
#include <linux/bitops.h>
#include <linux/delay.h>
#include <linux/mii.h>

#define MGMT_ID		0x00
#define MGMT_MDIO	0x04
#define MGMT_MDIO_IN	0x08
#define PKT_ID		0x10
#define PKT_CTRL	0x80
#define PKT_STATUS	0x84
#define PKT_TX_RING	0x88
#define PKT_RX_RING	0x8c
#define PKT_MASK	0x90
#define PKT_TX_PROD	0x94
#define PKT_TX_DONE	0x98
#define PKT_RX_PROD	0x9c
#define PKT_RX_CONS	0xa0
#define PKT_IRQ_PENDING	0xa4
#define PKT_IRQ_ENABLE	0xa8
#define PKT_FAULT	0xac

#define MGMT_ID_VALUE	0xae350e01
#define PKT_ID_VALUE	0xae350e03
#define CTRL_RUN	1
#define CTRL_RESET	2
#define STATUS_ACTIVE	3
#define IRQ_ALL		7

#define MDIO_RUN	BIT(0)
#define MDIO_MDC	BIT(1)
#define MDIO_LOW	BIT(2)

#define DESC_OWN	BIT(31)
#define DESC_DONE	BIT(30)
#define DESC_ERROR	BIT(29)

#define RTL8211F_ID	0x001cc916
#define RTL_PAGE	31
#define RTL_PAGE_MII	0xd08
#define RTL_TX_DELAY	0x11
#define RTL_RX_DELAY	0x15
#define RTL_TX_2NS	BIT(8)
#define RTL_RX_2NS	BIT(3)

#define RING		16
#define FRAME		1536
#define MAX_FRAME	1530
#define MIN_FRAME	60
#define GUARD		0xa5

struct ae350_desc {
	u32 buffer;
	u32 capacity;
	u32 length;
	u32 flags;
	u32 cookie;
	u32 reserved[3];
};

struct ae350_eth_priv {
	void __iomem *regs;
	unsigned int phy;
	struct ae350_desc *tx;
	struct ae350_desc *rx;
	u8 *tx_buffers;
	u8 *rx_buffers;
	u32 tx_prod;
	u32 rx_cons;
};

static u32 eth_read(struct ae350_eth_priv *priv, unsigned int reg)
{
	return readl(priv->regs + reg);
}

static void eth_write(struct ae350_eth_priv *priv, unsigned int reg, u32 value)
{
	writel(value, priv->regs + reg);
}

static void clean(const void *start, size_t bytes)
{
	ulong base = rounddown((ulong)start, ARCH_DMA_MINALIGN);

	flush_dcache_range(base, roundup((ulong)start + bytes, ARCH_DMA_MINALIGN));
}

static void discard(const void *start, size_t bytes)
{
	ulong base = rounddown((ulong)start, ARCH_DMA_MINALIGN);

	invalidate_dcache_range(base, roundup((ulong)start + bytes, ARCH_DMA_MINALIGN));
}

/*
 * The CPU AHB adapter posts the last written line; a NONSEQ read flushes it.
 * Reading the descriptor back after the clean publishes it to the MAC.
 */
static int publish(struct ae350_desc *desc)
{
	clean(desc, sizeof(*desc));
	discard(desc, sizeof(*desc));
	if (!(readl(&desc->flags) & DESC_OWN))
		return -EIO;
	wmb();
	return 0;
}

static unsigned int mdio_bit(struct ae350_eth_priv *priv, unsigned int value)
{
	u32 ctrl = MDIO_RUN | (value ? 0 : MDIO_LOW);
	unsigned int in;

	eth_write(priv, MGMT_MDIO, ctrl);
	udelay(1);
	eth_write(priv, MGMT_MDIO, ctrl | MDIO_MDC);
	udelay(1);
	in = eth_read(priv, MGMT_MDIO_IN) & 1;
	eth_write(priv, MGMT_MDIO, ctrl);
	return in;
}

static void mdio_send(struct ae350_eth_priv *priv, u32 value, unsigned int bits)
{
	while (bits--)
		mdio_bit(priv, (value >> bits) & 1);
}

static int mdio_read(struct ae350_eth_priv *priv, unsigned int phy,
		     unsigned int reg, u16 *value)
{
	unsigned int ack, data = 0;

	mdio_send(priv, ~0u, 32);
	mdio_send(priv, 6 << 10 | phy << 5 | reg, 14);
	ack = mdio_bit(priv, 1);
	for (int i = 0; i < 16; i++)
		data = data << 1 | mdio_bit(priv, 1);
	mdio_bit(priv, 1);
	*value = data;
	return ack ? -EIO : 0;
}

static void mdio_write(struct ae350_eth_priv *priv, unsigned int phy,
		       unsigned int reg, u16 value)
{
	mdio_send(priv, ~0u, 32);
	mdio_send(priv, 5u << 28 | phy << 23 | reg << 18 | 2 << 16 | value, 32);
	mdio_bit(priv, 1);
}

static int phy_id(struct ae350_eth_priv *priv, unsigned int phy, u32 *id)
{
	u16 high, low;

	if (mdio_read(priv, phy, MII_PHYSID1, &high) ||
	    mdio_read(priv, phy, MII_PHYSID2, &low))
		return -EIO;
	*id = high << 16 | low;
	return 0;
}

/* RGMII 2 ns TX and RX clock delays on page 0xd08 (Linux realtek.c). */
static int phy_delays(struct ae350_eth_priv *priv, bool program)
{
	unsigned int phy = priv->phy;
	u16 page, selected, tx, rx;
	int ret = 0;

	if (mdio_read(priv, phy, RTL_PAGE, &page) || page == 0xffff)
		return -EIO;
	mdio_write(priv, phy, RTL_PAGE, RTL_PAGE_MII);
	if (mdio_read(priv, phy, RTL_PAGE, &selected) || selected != RTL_PAGE_MII) {
		mdio_write(priv, phy, RTL_PAGE, page);
		return -EIO;
	}
	if (mdio_read(priv, phy, RTL_TX_DELAY, &tx) || mdio_read(priv, phy, RTL_RX_DELAY, &rx))
		ret = -EIO;
	if (!ret && program) {
		mdio_write(priv, phy, RTL_TX_DELAY, tx | RTL_TX_2NS);
		mdio_write(priv, phy, RTL_RX_DELAY, rx | RTL_RX_2NS);
		if (mdio_read(priv, phy, RTL_TX_DELAY, &tx) ||
		    mdio_read(priv, phy, RTL_RX_DELAY, &rx))
			ret = -EIO;
	}
	mdio_write(priv, phy, RTL_PAGE, page);
	if (!ret && (!(tx & RTL_TX_2NS) || !(rx & RTL_RX_2NS)))
		ret = -EIO;
	return ret;
}

static int phy_start(struct udevice *dev)
{
	struct ae350_eth_priv *priv = dev_get_priv(dev);
	unsigned int phy = priv->phy;
	ulong start;
	u16 value;
	int ret;

	ret = phy_delays(priv, true);
	if (ret)
		return ret;
	mdio_write(priv, phy, MII_BMCR, BMCR_RESET);
	start = get_timer(0);
	do {
		if (mdio_read(priv, phy, MII_BMCR, &value))
			return -EIO;
		if (get_timer(start) > 500)
			return -ETIMEDOUT;
	} while (value & BMCR_RESET);
	ret = phy_delays(priv, false);
	if (ret)
		return ret;
	if (mdio_read(priv, phy, MII_ADVERTISE, &value))
		return -EIO;
	mdio_write(priv, phy, MII_ADVERTISE, (value & ~ADVERTISE_ALL & ~ADVERTISE_PAUSE_CAP &
					      ~ADVERTISE_PAUSE_ASYM) | ADVERTISE_CSMA);
	if (mdio_read(priv, phy, MII_CTRL1000, &value))
		return -EIO;
	mdio_write(priv, phy, MII_CTRL1000, (value & ~ADVERTISE_1000HALF) | ADVERTISE_1000FULL);
	mdio_write(priv, phy, MII_BMCR, BMCR_ANENABLE | BMCR_ANRESTART);
	printf("%s: waiting for 1000BASE-T link", dev->name);
	start = get_timer(0);
	do {
		mdio_read(priv, phy, MII_BMSR, &value);
		if (!mdio_read(priv, phy, MII_BMSR, &value) &&
		    (value & (BMSR_LSTATUS | BMSR_ANEGCOMPLETE)) ==
		    (BMSR_LSTATUS | BMSR_ANEGCOMPLETE)) {
			printf(" up\n");
			return 0;
		}
		mdelay(100);
	} while (get_timer(start) < 10000);
	printf(" timed out\n");
	return -ETIMEDOUT;
}

static int dma_stop(struct ae350_eth_priv *priv)
{
	ulong start = get_timer(0);

	eth_write(priv, PKT_CTRL, 0);
	while (eth_read(priv, PKT_STATUS) & STATUS_ACTIVE)
		if (get_timer(start) > 100)
			return -ETIMEDOUT;
	return 0;
}

static int dma_start(struct ae350_eth_priv *priv)
{
	int ret = dma_stop(priv);

	if (ret)
		return ret;
	priv->tx_prod = 0;
	priv->rx_cons = 0;
	eth_write(priv, PKT_CTRL, CTRL_RESET);
	eth_write(priv, PKT_IRQ_ENABLE, 0);
	eth_write(priv, PKT_IRQ_PENDING, IRQ_ALL);
	memset(priv->tx, 0, RING * sizeof(*priv->tx));
	memset(priv->rx, 0, RING * sizeof(*priv->rx));
	memset(priv->tx_buffers, 0, RING * FRAME);
	memset(priv->rx_buffers, GUARD, RING * FRAME);
	for (int i = 0; i < RING; i++) {
		priv->tx[i].buffer = (ulong)(priv->tx_buffers + i * FRAME);
		priv->tx[i].capacity = FRAME;
		priv->tx[i].cookie = i;
		priv->rx[i].buffer = (ulong)(priv->rx_buffers + i * FRAME);
		priv->rx[i].capacity = FRAME;
		priv->rx[i].flags = DESC_OWN;
		priv->rx[i].cookie = i;
	}
	clean(priv->tx_buffers, RING * FRAME);
	clean(priv->rx_buffers, RING * FRAME);
	clean(priv->tx, RING * sizeof(*priv->tx));
	ret = publish(&priv->rx[RING - 1]);
	if (ret)
		return ret;
	eth_write(priv, PKT_TX_RING, (ulong)priv->tx);
	eth_write(priv, PKT_RX_RING, (ulong)priv->rx);
	eth_write(priv, PKT_MASK, RING - 1);
	wmb();
	eth_write(priv, PKT_CTRL, CTRL_RUN);
	return 0;
}

static int ae350_eth_start(struct udevice *dev)
{
	struct ae350_eth_priv *priv = dev_get_priv(dev);
	int ret = phy_start(dev);

	if (!ret)
		ret = dma_start(priv);
	return ret;
}

static int ae350_eth_send(struct udevice *dev, void *packet, int length)
{
	struct ae350_eth_priv *priv = dev_get_priv(dev);
	unsigned int slot = priv->tx_prod & (RING - 1);
	struct ae350_desc *desc = &priv->tx[slot];
	u8 *buffer = priv->tx_buffers + slot * FRAME;
	ulong start = get_timer(0);

	if (length > MAX_FRAME || eth_read(priv, PKT_FAULT))
		return -EINVAL;
	while (priv->tx_prod - eth_read(priv, PKT_TX_DONE) >= RING)
		if (get_timer(start) > 100)
			return -ETIMEDOUT;
	discard(desc, sizeof(*desc));
	if (desc->flags & (DESC_OWN | DESC_ERROR))
		return -EIO;
	memcpy(buffer, packet, length);
	if (length < MIN_FRAME) {
		memset(buffer + length, 0, MIN_FRAME - length);
		length = MIN_FRAME;
	}
	desc->length = length;
	desc->flags = DESC_OWN;
	clean(buffer, FRAME);
	if (publish(desc))
		return -EIO;
	eth_write(priv, PKT_TX_PROD, ++priv->tx_prod);
	start = get_timer(0);
	while (eth_read(priv, PKT_TX_DONE) != priv->tx_prod)
		if (get_timer(start) > 100)
			return -ETIMEDOUT;
	return 0;
}

static int ae350_eth_recv(struct udevice *dev, int flags, uchar **packetp)
{
	struct ae350_eth_priv *priv = dev_get_priv(dev);
	unsigned int slot = priv->rx_cons & (RING - 1);
	struct ae350_desc *desc = &priv->rx[slot];
	u8 *buffer = priv->rx_buffers + slot * FRAME;

	if (eth_read(priv, PKT_FAULT))
		return -EIO;
	if (priv->rx_cons == eth_read(priv, PKT_RX_PROD))
		return -EAGAIN;
	discard(desc, sizeof(*desc));
	if ((desc->flags & (DESC_OWN | DESC_DONE | DESC_ERROR)) != DESC_DONE ||
	    !desc->length || desc->length > MAX_FRAME)
		return -EIO;
	discard(buffer, FRAME);
	for (int i = MAX_FRAME; i < FRAME; i++)
		if (buffer[i] != GUARD)
			return -EIO;
	*packetp = buffer;
	return desc->length;
}

static int ae350_eth_free_pkt(struct udevice *dev, uchar *packet, int length)
{
	struct ae350_eth_priv *priv = dev_get_priv(dev);
	unsigned int slot = priv->rx_cons & (RING - 1);
	struct ae350_desc *desc = &priv->rx[slot];

	desc->length = 0;
	desc->flags = DESC_OWN;
	memset(priv->rx_buffers + slot * FRAME, GUARD, FRAME);
	clean(priv->rx_buffers + slot * FRAME, FRAME);
	if (publish(desc))
		return -EIO;
	eth_write(priv, PKT_RX_CONS, ++priv->rx_cons);
	return 0;
}

static void ae350_eth_stop(struct udevice *dev)
{
	dma_stop(dev_get_priv(dev));
}

static const struct eth_ops ae350_eth_ops = {
	.start		= ae350_eth_start,
	.send		= ae350_eth_send,
	.recv		= ae350_eth_recv,
	.free_pkt	= ae350_eth_free_pkt,
	.stop		= ae350_eth_stop,
};

static int ae350_eth_probe(struct udevice *dev)
{
	struct ae350_eth_priv *priv = dev_get_priv(dev);
	u32 id;

	priv->regs = dev_read_addr_ptr(dev);
	if (!priv->regs || eth_read(priv, MGMT_ID) != MGMT_ID_VALUE ||
	    eth_read(priv, PKT_ID) != PKT_ID_VALUE)
		return -ENODEV;
	priv->tx = memalign(ARCH_DMA_MINALIGN, RING * sizeof(*priv->tx));
	priv->rx = memalign(ARCH_DMA_MINALIGN, RING * sizeof(*priv->rx));
	priv->tx_buffers = memalign(ARCH_DMA_MINALIGN, RING * FRAME);
	priv->rx_buffers = memalign(ARCH_DMA_MINALIGN, RING * FRAME);
	if (!priv->tx || !priv->rx || !priv->tx_buffers || !priv->rx_buffers)
		return -ENOMEM;

	eth_write(priv, MGMT_MDIO, 0);
	mdelay(20);
	eth_write(priv, MGMT_MDIO, MDIO_RUN);
	mdelay(100);
	for (unsigned int index = 1; index <= 32; index++) {
		priv->phy = index & 31;
		if (!phy_id(priv, priv->phy, &id) && id == RTL8211F_ID)
			return 0;
	}
	dev_err(dev, "no RTL8211F on MDIO\n");
	return -ENODEV;
}

static const struct udevice_id ae350_eth_ids[] = {
	{ .compatible = "riscv-mini,ae350-eth" },
	{ }
};

U_BOOT_DRIVER(ae350_eth) = {
	.name		= "ae350_eth",
	.id		= UCLASS_ETH,
	.of_match	= ae350_eth_ids,
	.probe		= ae350_eth_probe,
	.ops		= &ae350_eth_ops,
	.priv_auto	= sizeof(struct ae350_eth_priv),
	.plat_auto	= sizeof(struct eth_pdata),
};
