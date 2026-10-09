// SPDX-License-Identifier: GPL-2.0+
/* riscv-mini LiteEth + RTL8201F. DMA builds exclusively own packet queues
 * through native DDR descriptor rings; PIO is a separate compile-time backend.
 * Ring register offsets are supplied by the selected SoC's device tree.
 */
#include <dm.h>
#include <dm/device_compat.h>
#include <log.h>
#include <mapmem.h>
#include <net.h>
#include <string.h>
#include <asm/io.h>
#include <linux/delay.h>
#include <linux/err.h>
#include <linux/ioport.h>
#include <linux/types.h>

#ifndef CONFIG_RISCV_MINI_ETH_RING_DMA
/* PIO ethmac CSR offsets */
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

#endif

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

#ifdef CONFIG_RISCV_MINI_ETH_RING_DMA
#define RING_COUNT 4
#define RING_BUFFER_SIZE 1536
#define RING_OWN BIT(31)
#define RING_DONE BIT(30)
#define RING_ERROR BIT(29)
enum {
	DMA_CONTROL, DMA_RX_BASE, DMA_TX_BASE, DMA_MASK, DMA_RX_CONSUMER,
	DMA_TX_PRODUCER, DMA_RX_PRODUCER, DMA_TX_CONSUMER, DMA_BUSY,
	DMA_ERROR, DMA_RX_PACKETS, DMA_TX_PACKETS, DMA_EV_PENDING,
	DMA_EV_ENABLE, DMA_REG_COUNT
};
struct liteeth_descriptor { u32 buffer, capacity, status, cookie; };
#endif

struct liteeth_priv {
	void __iomem *phy;
	int phy_addr;
	int holding;
	int rx_len;
#ifdef CONFIG_RISCV_MINI_ETH_RING_DMA
	void __iomem *dma;
	u32 offsets[DMA_REG_COUNT];
	u16 rx_consumer, tx_producer;
	volatile struct liteeth_descriptor rx_ring[RING_COUNT] __aligned(16);
	volatile struct liteeth_descriptor tx_ring[RING_COUNT] __aligned(16);
	u8 rx_pool[RING_COUNT][RING_BUFFER_SIZE] __aligned(16);
	u8 tx_pool[RING_COUNT][RING_BUFFER_SIZE] __aligned(16);
#else
	void __iomem *mac, *rx_base, *tx_base;
	int tx_slot;
	uchar rx_buf[ETH_MAX_FRAME];
#endif
};

#ifdef CONFIG_RISCV_MINI_ETH_RING_DMA
static u32 ring_read(struct liteeth_priv *p, unsigned reg)
{
	return in_le32(p->dma + p->offsets[reg]);
}
static void ring_write(struct liteeth_priv *p, unsigned reg, u32 value)
{
	out_le32(p->dma + p->offsets[reg], value);
}
static void ring_invalidate(void)
{
	/* Coherent shared L2; invalidate the VexRiscv private L1 before reads. */
	asm volatile("fence rw,rw\n.word 0x0000500f" ::: "memory");
}
static int ring_stop(struct liteeth_priv *p)
{
	ring_write(p, DMA_CONTROL, 0);
	ring_write(p, DMA_EV_ENABLE, 0);
	for (int i = 0; i < 10000; i++) {
		if (!ring_read(p, DMA_BUSY)) {
			p->holding = 0;
			return 0;
		}
		udelay(10);
	}
	return -ETIMEDOUT;
}
static void ring_init(struct liteeth_priv *p)
{
	p->rx_consumer = p->tx_producer = 0;
	p->holding = 0;
	for (int i = 0; i < RING_COUNT; i++) {
		p->rx_ring[i] = (struct liteeth_descriptor){
			(uintptr_t)p->rx_pool[i], RING_BUFFER_SIZE, RING_OWN, i};
		p->tx_ring[i] = (struct liteeth_descriptor){
			(uintptr_t)p->tx_pool[i], 0, 0, i};
	}
	wmb();
	ring_write(p, DMA_RX_BASE, (uintptr_t)p->rx_ring);
	ring_write(p, DMA_TX_BASE, (uintptr_t)p->tx_ring);
	ring_write(p, DMA_MASK, RING_COUNT - 1);
	ring_write(p, DMA_RX_CONSUMER, 0);
	ring_write(p, DMA_TX_PRODUCER, 0);
	ring_write(p, DMA_EV_PENDING, 3);
	ring_write(p, DMA_EV_ENABLE, 0); /* U-Boot polls; no interrupt handler. */
	ring_write(p, DMA_CONTROL, 1);
}
static void ring_release(struct liteeth_priv *p)
{
	p->rx_ring[p->rx_consumer & (RING_COUNT - 1)].status = RING_OWN;
	wmb();
	ring_write(p, DMA_RX_CONSUMER, ++p->rx_consumer);
	p->holding = 0;
}
#endif

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

#ifdef CONFIG_RISCV_MINI_ETH_RING_DMA
	ret = ring_stop(priv);
	if (ret)
		return ret;
#endif
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

#ifdef CONFIG_RISCV_MINI_ETH_RING_DMA
	ring_init(priv);
#else
	/* Arm the RX slot-released event and the TX-complete event. */
	out_le32(priv->mac + WR_EV_PENDING, 1);
	out_le32(priv->mac + WR_EV_ENABLE, 1);
	out_le32(priv->mac + RD_EV_PENDING, 1);
	out_le32(priv->mac + RD_EV_ENABLE, 1);
	priv->tx_slot = 0;
	priv->holding = 0;
#endif
	return 0;
}

static void liteeth_stop(struct udevice *dev)
{
	struct liteeth_priv *priv = dev_get_priv(dev);

#ifdef CONFIG_RISCV_MINI_ETH_RING_DMA
	if (ring_stop(priv)) {
		dev_err(dev, "DMA stop timed out\n");
		return;
	}
	out_le32(priv->phy + PHY_CRG_RESET, 1);
#else
	out_le32(priv->mac + WR_EV_ENABLE, 0);
	out_le32(priv->mac + RD_EV_ENABLE, 0);
#endif
}

#ifdef CONFIG_RISCV_MINI_ETH_RING_DMA
static int liteeth_send(struct udevice *dev, void *packet, int length)
{
	struct liteeth_priv *p = dev_get_priv(dev);
	u16 target;
	unsigned slot;

	if (length < 14 || length > ETH_MAX_FRAME)
		return -EINVAL;
	if ((u16)(p->tx_producer - (u16)ring_read(p, DMA_TX_CONSUMER)) >= RING_COUNT)
		return -EBUSY;
	slot = p->tx_producer & (RING_COUNT - 1);
	memcpy(p->tx_pool[slot], packet, length);
	p->tx_ring[slot].capacity = length;
	p->tx_ring[slot].status = RING_OWN;
	wmb();
	target = ++p->tx_producer;
	ring_write(p, DMA_TX_PRODUCER, target);
	for (int i = 0; i < 10000; i++) {
		if ((u16)ring_read(p, DMA_TX_CONSUMER) == target) {
			ring_invalidate();
			return (p->tx_ring[slot].status & RING_ERROR) ? -EIO : 0;
		}
		udelay(10);
	}
	return -ETIMEDOUT;
}
static int liteeth_recv(struct udevice *dev, int flags, uchar **packetp)
{
	struct liteeth_priv *p = dev_get_priv(dev);
	u32 status;
	unsigned slot, length;

	if (p->holding || (u16)ring_read(p, DMA_RX_PRODUCER) == p->rx_consumer)
		return -EAGAIN;
	ring_invalidate();
	slot = p->rx_consumer & (RING_COUNT - 1);
	status = p->rx_ring[slot].status;
	if ((status & RING_OWN) || !(status & RING_DONE))
		return -EIO;
	length = status & 0xfff;
	if ((status & RING_ERROR) || length < 14 || length > ETH_MAX_FRAME) {
		ring_release(p);
		return -EAGAIN;
	}
	p->holding = 1;
	p->rx_len = length;
	*packetp = p->rx_pool[slot];
	return length;
}
static int liteeth_free_pkt(struct udevice *dev, uchar *packet, int length)
{
	struct liteeth_priv *p = dev_get_priv(dev);

	if (!p->holding || packet != p->rx_pool[p->rx_consumer & (RING_COUNT - 1)] ||
	    length != p->rx_len)
		return -EINVAL;
	ring_release(p);
	return 0;
}
#else
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

#endif

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
#ifdef CONFIG_RISCV_MINI_ETH_RING_DMA
	if (dev_read_resource(dev, 0, &res))
		return -EINVAL;
	priv->dma = map_sysmem(res.start, 0);
	if (dev_read_u32_array(dev, "riscv-mini,csr-offsets", priv->offsets, DMA_REG_COUNT))
		return -EINVAL;
	for (unsigned i = 0; i < DMA_REG_COUNT; i++)
		if ((priv->offsets[i] & 3) || priv->offsets[i] + 4 > resource_size(&res))
			return -EINVAL;
	if (dev_read_resource(dev, 1, &res))
		return -EINVAL;
	priv->phy = map_sysmem(res.start, 0);
#else
	void __iomem *maps[4];
	for (int i = 0; i < 4; i++) {
		if (dev_read_resource(dev, i, &res))
			return -EINVAL;
		maps[i] = map_sysmem(res.start, 0);
	}
	priv->mac = maps[0];
	priv->phy = maps[1];
	priv->rx_base = maps[2];
	priv->tx_base = maps[3];
#endif
	return 0;
}

static const struct udevice_id liteeth_ids[] = {
#ifdef CONFIG_RISCV_MINI_ETH_RING_DMA
	{ .compatible = "riscv-mini,liteeth-ring" },
#else
	{ .compatible = "riscv-mini,liteeth" },
#endif
	{ }
};

static int liteeth_remove(struct udevice *dev)
{
#ifdef CONFIG_RISCV_MINI_ETH_RING_DMA
	struct liteeth_priv *p = dev_get_priv(dev);
	int ret = ring_stop(p);
	if (ret)
		return ret;
	out_le32(p->phy + PHY_CRG_RESET, 1);
#else
	liteeth_stop(dev);
#endif
	return 0;
}

U_BOOT_DRIVER(liteeth) = {
	.name		= "liteeth",
	.id		= UCLASS_ETH,
	.of_match	= liteeth_ids,
	.of_to_plat	= liteeth_of_to_plat,
	.ops		= &liteeth_ops,
	.remove		= liteeth_remove,
	.flags		= DM_FLAG_ALLOC_PRIV_DMA | DM_FLAG_OS_PREPARE,
	.priv_auto	= sizeof(struct liteeth_priv),
	.plat_auto	= sizeof(struct eth_pdata),
};
