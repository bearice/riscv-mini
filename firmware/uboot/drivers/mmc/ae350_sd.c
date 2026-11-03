// SPDX-License-Identifier: GPL-2.0+
/*
 * SD host of the riscv-mini AE350 machine. The native LiteSDCard command
 * core (reg "core") exposes its block data as one TX and one RX FIFO word
 * (reg "tx", "rx"); a channel of the hard ATCDMAC300 moves that data to and
 * from a DDR bounce buffer under the core's DMA request handshake. The
 * channel and request selectors come from the dmas property
 * (<&dmac channel selector>). U-Boot's MMC core runs the SD protocol; this
 * driver executes one command and its data phase.
 */
#include <cpu_func.h>
#include <dm.h>
#include <dm/device_compat.h>
#include <dm/ofnode.h>
#include <malloc.h>
#include <mapmem.h>
#include <mmc.h>
#include <time.h>
#include <asm/cache.h>
#include <asm/io.h>
#include <linux/bitops.h>
#include <linux/delay.h>
#include <linux/ioport.h>

#define SD_CTRL		0x0c
#define SD_SOURCE_HZ	0x20
#define SD_CD		0x40
#define SD_DIV		0x44
#define SD_WIDTH	0x48
#define SD_INIT		0x4c
#define SD_CMD_TO	0x50
#define SD_DATA_TO	0x54
#define SD_ARG		0x80
#define SD_CMD		0x84
#define SD_SEND		0x88
#define SD_RESP		0x8c	/* four words, most significant first */
#define SD_CMD_EVENT	0x9c
#define SD_DATA_EVENT	0xa0
#define SD_BLK_LEN	0xa4
#define SD_BLK_CNT	0xa8

#define CTRL_RESET	BIT(0)
#define CTRL_DMA	BIT(1)
#define CD_ABSENT	BIT(0)
#define EVENT_DONE	BIT(0)
#define EVENT_ERRORS	(BIT(1) | BIT(2) | BIT(3))

#define RESP_NONE	0
#define RESP_SHORT	1
#define RESP_LONG	2
#define RESP_BUSY	3
#define RESP_CRC	4
#define DATA_READ	32
#define DATA_WRITE	64

#define DMAC_ABORT	0x24
#define DMAC_STATUS	0x30	/* error[7:0] abort[15:8] tc[23:16], W1C */
#define DMAC_BUSY	0x34
#define DMAC_CH(n)	(0x40 + (n) * 0x20)
#define CH_CTRL		0x00
#define CH_SIZE		0x04
#define CH_SRC		0x08
#define CH_SRC_HI	0x0c
#define CH_DST		0x10
#define CH_DST_HI	0x14
#define CH_LLP		0x18
#define CH_LLP_HI	0x1c

#define CH_ENABLE	BIT(0)
#define CH_IRQ_MASKS	(BIT(1) | BIT(2) | BIT(3))
#define CH_WORDS	((2 << 21) | (2 << 18))
#define CH_SRC_FIXED	(2 << 14)
#define CH_DST_FIXED	(2 << 12)
#define CH_DST_HS	BIT(16)
#define CH_SRC_HS	BIT(17)
#define CH_DST_REQ(s)	((s) << 4)
#define CH_SRC_REQ(s)	((s) << 8)

#define DEFAULT_HZ	25000000u
#define IDENT_HZ	400000u
/* The core's block count register is 4 bits wide; 8 matches the BSP transfer. */
#define MAX_BLOCKS	8
#define TIMEOUT_MS	1000

struct ae350_sd_priv {
	void __iomem *regs;
	void __iomem *dmac;
	phys_addr_t tx_fifo;
	phys_addr_t rx_fifo;
	unsigned int channel;
	unsigned int tx_select;
	unsigned int rx_select;
	unsigned int source_hz;
	unsigned int clock_hz;
	u8 *bounce;
	bool dma_owned;
};

struct ae350_sd_plat {
	struct mmc_config cfg;
	struct mmc mmc;
};

static u32 sd_read(struct ae350_sd_priv *priv, unsigned int reg)
{
	return readl(priv->regs + reg);
}

static void sd_write(struct ae350_sd_priv *priv, unsigned int reg, u32 value)
{
	writel(value, priv->regs + reg);
}

static void __iomem *channel_reg(struct ae350_sd_priv *priv, unsigned int reg)
{
	return priv->dmac + DMAC_CH(priv->channel) + reg;
}

static bool present(struct ae350_sd_priv *priv)
{
	return !(sd_read(priv, SD_CD) & CD_ABSENT);
}

/* Disable the channel and wait until the DMAC reports it idle. */
static int dma_stop(struct ae350_sd_priv *priv)
{
	u32 bit = BIT(priv->channel);
	ulong start = get_timer(0);

	sd_write(priv, SD_CTRL, 0);
	if (!priv->dma_owned)
		return 0;
	if (readl(priv->dmac + DMAC_BUSY) & bit)
		writel(bit, priv->dmac + DMAC_ABORT);
	writel(readl(channel_reg(priv, CH_CTRL)) & ~CH_ENABLE, channel_reg(priv, CH_CTRL));
	while (readl(priv->dmac + DMAC_BUSY) & bit)
		if (get_timer(start) > TIMEOUT_MS)
			return -ETIMEDOUT;
	writel(bit * 0x10101, priv->dmac + DMAC_STATUS);
	priv->dma_owned = false;
	return 0;
}

static void controller_reset(struct ae350_sd_priv *priv)
{
	dma_stop(priv);
	sd_write(priv, SD_CTRL, CTRL_RESET);
	udelay(1000);
	sd_write(priv, SD_CTRL, 0);
	udelay(1000);
}

static int dma_start(struct ae350_sd_priv *priv, bool write, unsigned int bytes)
{
	u32 bit = BIT(priv->channel);
	ulong buffer = (ulong)priv->bounce;
	u32 ctrl = CH_WORDS | CH_IRQ_MASKS;

	if (readl(priv->dmac + DMAC_BUSY) & bit)
		return -EBUSY;
	flush_dcache_range(buffer, buffer + roundup(bytes, ARCH_DMA_MINALIGN));
	if (write)
		ctrl |= CH_DST_FIXED | CH_DST_HS | CH_DST_REQ(priv->tx_select);
	else
		ctrl |= CH_SRC_FIXED | CH_SRC_HS | CH_SRC_REQ(priv->rx_select);
	writel(bit * 0x10101, priv->dmac + DMAC_STATUS);
	writel(0, channel_reg(priv, CH_CTRL));
	writel(bytes / 4, channel_reg(priv, CH_SIZE));
	writel(write ? buffer : priv->rx_fifo, channel_reg(priv, CH_SRC));
	writel(0, channel_reg(priv, CH_SRC_HI));
	writel(write ? priv->tx_fifo : buffer, channel_reg(priv, CH_DST));
	writel(0, channel_reg(priv, CH_DST_HI));
	writel(0, channel_reg(priv, CH_LLP));
	writel(0, channel_reg(priv, CH_LLP_HI));
	writel(ctrl | CH_ENABLE, channel_reg(priv, CH_CTRL));
	priv->dma_owned = true;
	sd_write(priv, SD_CTRL, CTRL_DMA);
	return 0;
}

/* Error and abort take precedence; completion needs idle plus terminal count. */
static int dma_finish(struct ae350_sd_priv *priv, bool write, unsigned int bytes)
{
	u32 bit = BIT(priv->channel);
	ulong buffer = (ulong)priv->bounce;
	ulong start = get_timer(0);
	u32 status;
	bool busy;

	do {
		busy = readl(priv->dmac + DMAC_BUSY) & bit;
		status = readl(priv->dmac + DMAC_STATUS);
		if (status & (bit | bit << 8))
			return -EIO;
		if (!busy)
			break;
	} while (get_timer(start) < TIMEOUT_MS);
	if (busy || !(status & bit << 16))
		return -ETIMEDOUT;
	sd_write(priv, SD_CTRL, 0);
	writel(bit * 0x10101, priv->dmac + DMAC_STATUS);
	priv->dma_owned = false;
	if (!write)
		invalidate_dcache_range(buffer, buffer + roundup(bytes, ARCH_DMA_MINALIGN));
	return 0;
}

static int wait_event(struct ae350_sd_priv *priv, unsigned int reg)
{
	ulong start = get_timer(0);

	do {
		u32 event = sd_read(priv, reg);

		if (event & EVENT_DONE)
			return event & EVENT_ERRORS ? -EIO : 0;
		if (!present(priv))
			return -ENOMEDIUM;
	} while (get_timer(start) < TIMEOUT_MS);
	return -ETIMEDOUT;
}

static unsigned int response_flags(uint type, bool data)
{
	unsigned int flags = RESP_NONE;

	if (type & MMC_RSP_136)
		flags = RESP_LONG;
	else if ((type & MMC_RSP_BUSY) && !data)
		flags = RESP_BUSY;
	else if (type & MMC_RSP_PRESENT)
		flags = RESP_SHORT;
	if (type & MMC_RSP_CRC)
		flags |= RESP_CRC;
	return flags;
}

static int ae350_sd_send_cmd(struct udevice *dev, struct mmc_cmd *cmd,
			     struct mmc_data *data)
{
	struct ae350_sd_priv *priv = dev_get_priv(dev);
	unsigned int flags = response_flags(cmd->resp_type, data);
	unsigned int bytes = 0;
	bool write = false;
	int ret;

	if (!present(priv))
		return -ENOMEDIUM;
	if (data) {
		bytes = data->blocks * data->blocksize;
		if (!bytes || bytes > MAX_BLOCKS * 512 || bytes % 4)
			return -EINVAL;
		write = data->flags & MMC_DATA_WRITE;
		flags |= write ? DATA_WRITE : DATA_READ;
		if (write)
			memcpy(priv->bounce, data->src, bytes);
		sd_write(priv, SD_BLK_LEN, data->blocksize);
		sd_write(priv, SD_BLK_CNT, data->blocks);
		ret = dma_start(priv, write, bytes);
		if (ret)
			return ret;
	}

	sd_write(priv, SD_ARG, cmd->cmdarg);
	sd_write(priv, SD_CMD, cmd->cmdidx << 8 | flags);
	sd_write(priv, SD_SEND, 1);
	ret = wait_event(priv, SD_CMD_EVENT);
	if (ret)
		goto fail;

	if (cmd->resp_type & MMC_RSP_136) {
		for (int i = 0; i < 4; i++)
			cmd->response[i] = sd_read(priv, SD_RESP + 4 * i);
	} else if (cmd->resp_type & MMC_RSP_PRESENT) {
		cmd->response[0] = sd_read(priv, SD_RESP + 12);
	}

	if (data) {
		ret = wait_event(priv, SD_DATA_EVENT);
		if (!ret)
			ret = dma_finish(priv, write, bytes);
		if (ret)
			goto fail;
		if (!write)
			memcpy(data->dest, priv->bounce, bytes);
	}
	return 0;

fail:
	if (data || ret != -ETIMEDOUT)
		dev_dbg(dev, "CMD%u: %d cmd_event=%x data_event=%x dma=%x\n",
			cmd->cmdidx, ret, sd_read(priv, SD_CMD_EVENT),
			sd_read(priv, SD_DATA_EVENT), readl(priv->dmac + DMAC_STATUS));
	if (data && dma_stop(priv))
		controller_reset(priv);
	return ret;
}

/* The PHY divides by an even count: hz = source / (2 * ((div + 1) / 2)). */
static int ae350_sd_set_ios(struct udevice *dev)
{
	struct mmc *mmc = mmc_get_mmc_dev(dev);
	struct ae350_sd_priv *priv = dev_get_priv(dev);
	unsigned int hz = clamp(mmc->clock ? mmc->clock : IDENT_HZ,
				mmc->cfg->f_min, mmc->cfg->f_max);
	unsigned int divider = clamp(DIV_ROUND_UP(priv->source_hz, hz), 2u, 511u);

	if (hz != priv->clock_hz) {
		sd_write(priv, SD_DIV, divider);
		priv->clock_hz = hz;
		udelay(1000);
	}
	sd_write(priv, SD_WIDTH, mmc->bus_width == 4);
	return 0;
}

static int ae350_sd_get_cd(struct udevice *dev)
{
	return present(dev_get_priv(dev));
}

static void ae350_sd_init_stream(struct udevice *dev)
{
	struct ae350_sd_priv *priv = dev_get_priv(dev);

	sd_write(priv, SD_INIT, 1);
	udelay(2000);
}

static int ae350_sd_deferred_probe(struct udevice *dev)
{
	struct ae350_sd_priv *priv = dev_get_priv(dev);

	controller_reset(priv);
	sd_write(priv, SD_DIV, DIV_ROUND_UP(priv->source_hz, IDENT_HZ));
	sd_write(priv, SD_WIDTH, 0);
	sd_write(priv, SD_CMD_TO, priv->source_hz / 4);
	sd_write(priv, SD_DATA_TO, priv->source_hz / 2);
	priv->clock_hz = IDENT_HZ;
	return 0;
}

static const struct dm_mmc_ops ae350_sd_ops = {
	.send_cmd		= ae350_sd_send_cmd,
	.set_ios		= ae350_sd_set_ios,
	.get_cd			= ae350_sd_get_cd,
	.send_init_stream	= ae350_sd_init_stream,
	.deferred_probe		= ae350_sd_deferred_probe,
};

static int read_dma(struct udevice *dev, const char *name, ofnode *dmac,
		    unsigned int *channel, unsigned int *select)
{
	struct ofnode_phandle_args args;
	int index = dev_read_stringlist_search(dev, "dma-names", name);

	if (index < 0 || dev_read_phandle_with_args(dev, "dmas", "#dma-cells", 0,
						    index, &args) || args.args_count != 2)
		return -EINVAL;
	*dmac = args.node;
	*channel = args.args[0];
	*select = args.args[1];
	return 0;
}

static int ae350_sd_of_to_plat(struct udevice *dev)
{
	struct ae350_sd_priv *priv = dev_get_priv(dev);
	unsigned int tx_channel, rx_channel;
	ofnode tx_dmac, rx_dmac;
	fdt_addr_t dmac;

	priv->regs = dev_read_addr_name_ptr(dev, "core");
	priv->tx_fifo = dev_read_addr_name(dev, "tx");
	priv->rx_fifo = dev_read_addr_name(dev, "rx");
	if (!priv->regs || priv->tx_fifo == FDT_ADDR_T_NONE ||
	    priv->rx_fifo == FDT_ADDR_T_NONE)
		return -EINVAL;
	if (read_dma(dev, "tx", &tx_dmac, &tx_channel, &priv->tx_select) ||
	    read_dma(dev, "rx", &rx_dmac, &rx_channel, &priv->rx_select) ||
	    !ofnode_equal(tx_dmac, rx_dmac) || tx_channel != rx_channel || tx_channel > 7)
		return -EINVAL;
	dmac = ofnode_get_addr(tx_dmac);
	if (dmac == FDT_ADDR_T_NONE)
		return -EINVAL;
	priv->dmac = map_sysmem(dmac, 0);
	priv->channel = tx_channel;
	priv->bounce = memalign(ARCH_DMA_MINALIGN, MAX_BLOCKS * 512);
	return priv->bounce ? 0 : -ENOMEM;
}

static int ae350_sd_probe(struct udevice *dev)
{
	struct ae350_sd_plat *plat = dev_get_plat(dev);
	struct ae350_sd_priv *priv = dev_get_priv(dev);
	struct mmc_uclass_priv *upriv = dev_get_uclass_priv(dev);

	priv->source_hz = sd_read(priv, SD_SOURCE_HZ);
	if (priv->source_hz < 2 * IDENT_HZ)
		return -ENODEV;
	plat->cfg.f_max = min(DEFAULT_HZ, priv->source_hz / 2);
	upriv->mmc = &plat->mmc;
	return 0;
}

static int ae350_sd_bind(struct udevice *dev)
{
	struct ae350_sd_plat *plat = dev_get_plat(dev);

	plat->cfg.name = dev->name;
	plat->cfg.host_caps = MMC_MODE_4BIT;
	plat->cfg.voltages = MMC_VDD_32_33 | MMC_VDD_33_34;
	plat->cfg.f_min = IDENT_HZ;
	plat->cfg.f_max = DEFAULT_HZ;
	plat->cfg.b_max = MAX_BLOCKS;
	return mmc_bind(dev, &plat->mmc, &plat->cfg);
}

static const struct udevice_id ae350_sd_ids[] = {
	{ .compatible = "riscv-mini,ae350-sd" },
	{ }
};

U_BOOT_DRIVER(ae350_sd) = {
	.name		= "ae350_sd",
	.id		= UCLASS_MMC,
	.of_match	= ae350_sd_ids,
	.bind		= ae350_sd_bind,
	.of_to_plat	= ae350_sd_of_to_plat,
	.probe		= ae350_sd_probe,
	.ops		= &ae350_sd_ops,
	.priv_auto	= sizeof(struct ae350_sd_priv),
	.plat_auto	= sizeof(struct ae350_sd_plat),
};
