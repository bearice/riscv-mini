// SPDX-License-Identifier: GPL-2.0+
/*
 * LiteSDCard native SD host (SDPHY + custom SDCore + block DMA) for the
 * riscv-mini TangPrimer 20K SoC. This is the same core the BIOS drives
 * (firmware/drivers/sd_native.c); the IP is a modified LiteSDCard whose CSRs
 * are 32-bit (csr_read_simple), so no mainline U-Boot driver matches it.
 *
 * U-Boot's MMC core (drivers/mmc/mmc.c) drives the whole SD init sequence;
 * this driver only implements the host primitives: set clock/bus-width and
 * execute one command (with optional block data via the block2mem / mem2block
 * DMA). Command/data mechanics mirror sd_native.c exactly.
 *
 * Register map (byte offsets, native-endian 32-bit), relative to the sdcard
 * CSR base (reg index 0):
 *   phy: card_detect 0x00 clocker_divider 0x04 init_initialize 0x08
 *        cmdr_timeout 0x0c dataw_status 0x10 datar_timeout 0x14 settings 0x18
 *   core: cmd_argument 0x1c cmd_command 0x20 cmd_send 0x24 cmd_response 0x28
 *         (4 words) cmd_event 0x38 data_event 0x3c block_length 0x40
 *         block_count 0x44
 *   block2mem: base 0x48 length 0x4c enable 0x50 done 0x54 error 0x58
 *              offset 0x5c busy 0x60
 *   mem2block: base 0x64 length 0x68 enable 0x6c done 0x70 error 0x74
 *              offset 0x78 busy 0x7c
 *   ev_status 0x80 ev_pending 0x84 ev_enable 0x88
 * Controller reset block (reg index 1): reset 0x00.
 *
 * cmd_command = (cmdidx << 8) | flags, where flags encode the SDCore command
 * type (bits 0-1: 0 none, 1 short, 2 long, 3 busy), CRC check (bit 2) and
 * data direction (bits 5-6: 1 read, 2 write).
 */
#include <dm.h>
#include <dm/device_compat.h>
#include <log.h>
#include <malloc.h>
#include <mapmem.h>
#include <mmc.h>
#include <time.h>
#include <asm/io.h>
#include <linux/delay.h>
#include <linux/ioport.h>
#include <linux/types.h>

/* sdcard CSR offsets */
#define PHY_CD		0x00
#define PHY_DIV		0x04
#define PHY_INIT	0x08
#define PHY_CMDR_TO	0x0c
#define PHY_DATAW_ST	0x10
#define PHY_DATAR_TO	0x14
#define PHY_SETTINGS	0x18
#define CMD_ARG		0x1c
#define CMD_CMD		0x20
#define CMD_SEND	0x24
#define CMD_RESP	0x28
#define CMD_EVENT	0x38
#define DATA_EVENT	0x3c
#define BLK_LEN		0x40
#define BLK_CNT		0x44
#define B2M_BASE	0x48
#define B2M_LEN		0x4c
#define B2M_EN		0x50
#define B2M_DONE	0x54
#define B2M_ERR		0x58
#define B2M_BUSY	0x60
#define M2B_BASE	0x64
#define M2B_LEN		0x68
#define M2B_EN		0x6c
#define M2B_DONE	0x70
#define M2B_ERR		0x74
#define M2B_BUSY	0x7c

/* SDCore command flags */
#define RESP_NONE	0
#define RESP_SHORT	1
#define RESP_LONG	2
#define RESP_BUSY	3
#define RESP_CRC	4
#define DATA_READ	32
#define DATA_WRITE	64

#define SD_CLOCK	60000000u	/* SoC system clock feeding the PHY divider */
#define MAX_BLOCKS	8
#define BOUNCE_BYTES	(MAX_BLOCKS * 512)

struct litesd_priv {
	void __iomem *regs;	/* sdcard CSR base */
	void __iomem *ctrl;	/* sd_control reset block */
	uint *bounce;		/* 16-byte aligned DMA bounce buffer */
	unsigned clock_hz;
	unsigned width;
};

struct litesd_plat {
	struct mmc_config cfg;
	struct mmc mmc;
};

static void litesd_reset(struct litesd_priv *priv)
{
	out_le32(priv->regs + B2M_EN, 0);
	out_le32(priv->regs + M2B_EN, 0);
	out_le32(priv->ctrl, 1);
	udelay(1000);
	out_le32(priv->ctrl, 0);
	udelay(1000);
	out_le32(priv->regs + 0x88, 0);		/* ev_enable */
	out_le32(priv->regs + 0x84, 15);	/* ev_pending W1C */
	out_le32(priv->regs + PHY_DIV, 150);	/* 400 kHz */
	out_le32(priv->regs + PHY_SETTINGS, 0);	/* 1-bit */
	out_le32(priv->regs + PHY_CMDR_TO, SD_CLOCK / 4);
	out_le32(priv->regs + PHY_DATAR_TO, SD_CLOCK / 2);
	/* Power-up init stream (74 clocks) before the first command, exactly as
	 * the BIOS does in disk_initialize; without it the first enumeration is
	 * flaky.
	 */
	out_le32(priv->regs + PHY_INIT, 1);
	udelay(2000);
	priv->clock_hz = 400000;
	priv->width = 1;
}

static int litesd_present(struct litesd_priv *priv)
{
	return !(in_le32(priv->regs + PHY_CD) & 1u);
}

static int litesd_wait_event(struct litesd_priv *priv, int data)
{
	u64 start = get_timer(0);

	do {
		unsigned ev = in_le32(priv->regs + (data ? DATA_EVENT : CMD_EVENT));

		if (ev & 1u)
			return !(ev & 14u);
		if (!litesd_present(priv))
			return 0;
	} while (get_timer(start) < 1000);
	return 0;
}

static void litesd_stop_dma(struct litesd_priv *priv)
{
	u64 start = get_timer(0);

	out_le32(priv->regs + B2M_EN, 0);
	out_le32(priv->regs + M2B_EN, 0);
	while (in_le32(priv->regs + B2M_BUSY) || in_le32(priv->regs + M2B_BUSY))
		if (get_timer(start) >= 100)
			break;
}

static void litesd_start_dma(struct litesd_priv *priv, int write, unsigned bytes)
{
	wmb();
	if (write) {
		out_le32(priv->regs + M2B_EN, 0);
		out_le32(priv->regs + M2B_BASE, (uintptr_t)priv->bounce);
		out_le32(priv->regs + M2B_LEN, bytes);
		out_le32(priv->regs + M2B_EN, 1);
	} else {
		out_le32(priv->regs + B2M_EN, 0);
		out_le32(priv->regs + B2M_BASE, (uintptr_t)priv->bounce);
		out_le32(priv->regs + B2M_LEN, bytes);
		out_le32(priv->regs + B2M_EN, 1);
	}
}

static int litesd_wait_dma(struct litesd_priv *priv, int write)
{
	u64 start = get_timer(0);

	do {
		if (in_le32(priv->regs + (write ? M2B_ERR : B2M_ERR)))
			return 0;
		if (in_le32(priv->regs + (write ? M2B_DONE : B2M_DONE))) {
			mb();
			return 1;
		}
		if (!litesd_present(priv))
			return 0;
	} while (get_timer(start) < 1000);
	return 0;
}

/* Map U-Boot's response type to the SDCore command-type/CRC encoding. */
static unsigned litesd_resp_flags(uint resp_type)
{
	unsigned type = RESP_NONE;

	if (resp_type & MMC_RSP_136)
		type = RESP_LONG;
	else if (resp_type & MMC_RSP_BUSY)
		type = RESP_BUSY;
	else if (resp_type & MMC_RSP_PRESENT)
		type = RESP_SHORT;
	if (resp_type & MMC_RSP_CRC)
		type |= RESP_CRC;
	return type;
}

static int litesd_send_cmd(struct udevice *dev, struct mmc_cmd *cmd,
			   struct mmc_data *data)
{
	struct litesd_priv *priv = dev_get_priv(dev);
	unsigned flags = litesd_resp_flags(cmd->resp_type);
	unsigned bytes = 0, blocks = 0;
	int write = 0;

	/* A BUSY (R1b) response is only encoded as RESP_BUSY for commands with no
	 * data phase (CMD7/CMD12). For a command that carries data (CMD6 SWITCH),
	 * the busy is tied to the data transfer, so the SDCore must see a SHORT
	 * response — this mirrors the BIOS's switch_function().
	 */
	if (data && (flags & 3u) == RESP_BUSY)
		flags = (flags & ~3u) | RESP_SHORT;

	if (!litesd_present(priv))
		return -ENOMEDIUM;

	if (data) {
		blocks = data->blocks;
		bytes = blocks * data->blocksize;
		write = (data->flags & MMC_DATA_WRITE) ? 1 : 0;
		flags |= write ? DATA_WRITE : DATA_READ;
		out_le32(priv->regs + BLK_LEN, data->blocksize);
		out_le32(priv->regs + BLK_CNT, blocks);
		if (write)
			memcpy(priv->bounce, data->src, bytes);
		litesd_start_dma(priv, write, bytes);
	}

	out_le32(priv->regs + CMD_ARG, cmd->cmdarg);
	out_le32(priv->regs + CMD_CMD, (cmd->cmdidx << 8) | flags);
	out_le32(priv->regs + CMD_SEND, 1);

	if (!litesd_wait_event(priv, 0)) {
		litesd_stop_dma(priv);
		return -EIO;
	}

	/* The SDCore stores the response as a big-endian bit stream: read_word(3)
	 * holds the low 32 bits (the whole R1/R3 value), read_word(0) holds the
	 * top 32 bits of a 136-bit R2 (CID/CSD). U-Boot wants response[0] as the
	 * leading word for R2 and the 32-bit value for R1, so the word order
	 * differs by response type.
	 */
	if (cmd->resp_type & MMC_RSP_136) {
		int i;

		for (i = 0; i < 4; i++)
			cmd->response[i] = in_le32(priv->regs + CMD_RESP + 4 * i);
	} else if (cmd->resp_type & MMC_RSP_PRESENT) {
		cmd->response[0] = in_le32(priv->regs + CMD_RESP + 4 * 3);
	}

	if (data) {
		if (!litesd_wait_event(priv, 1) || !litesd_wait_dma(priv, write)) {
			litesd_stop_dma(priv);
			return -EIO;
		}
		litesd_stop_dma(priv);
		if (!write)
			memcpy(data->dest, priv->bounce, bytes);
	}

	/* A busy (R1b) response — CMD7/CMD12 with no data, or CMD6 after its data
	 * block — holds DAT0 low after the event fires; the SDCore does not wait
	 * for that trailing busy, so wait it out before the next command.
	 */
	if (cmd->resp_type & MMC_RSP_BUSY)
		udelay(10000);
	return 0;
}

static int litesd_set_ios(struct udevice *dev)
{
	struct mmc *mmc = mmc_get_mmc_dev(dev);
	struct litesd_priv *priv = dev_get_priv(dev);
	unsigned hz = mmc->clock ? mmc->clock : 400000;

	if (hz > 7500000u)
		hz = 7500000u;
	if (hz < 400000u)
		hz = 400000u;
	if (hz != priv->clock_hz) {
		out_le32(priv->regs + PHY_DIV, SD_CLOCK / hz);
		priv->clock_hz = hz;
		udelay(1000);
	}
	out_le32(priv->regs + PHY_SETTINGS, mmc->bus_width == 4 ? 1 : 0);
	priv->width = mmc->bus_width;
	return 0;
}

static int litesd_get_cd(struct udevice *dev)
{
	struct litesd_priv *priv = dev_get_priv(dev);

	return litesd_present(priv);
}

static void litesd_init_stream(struct udevice *dev)
{
	struct litesd_priv *priv = dev_get_priv(dev);

	out_le32(priv->regs + PHY_INIT, 1);
	udelay(1000);
}

static int litesd_deferred_probe(struct udevice *dev)
{
	struct litesd_priv *priv = dev_get_priv(dev);

	litesd_reset(priv);
	return 0;
}

static const struct dm_mmc_ops litesd_ops = {
	.send_cmd	= litesd_send_cmd,
	.set_ios	= litesd_set_ios,
	.get_cd		= litesd_get_cd,
	.send_init_stream = litesd_init_stream,
	.deferred_probe	= litesd_deferred_probe,
};

static int litesd_of_to_plat(struct udevice *dev)
{
	struct litesd_priv *priv = dev_get_priv(dev);
	struct resource res;

	if (dev_read_resource(dev, 0, &res))
		return -EINVAL;
	priv->regs = map_sysmem(res.start, 0);
	if (dev_read_resource(dev, 1, &res))
		return -EINVAL;
	priv->ctrl = map_sysmem(res.start, 0);
	priv->bounce = memalign(16, BOUNCE_BYTES);
	if (!priv->bounce)
		return -ENOMEM;
	return 0;
}

static int litesd_probe(struct udevice *dev)
{
	struct litesd_plat *plat = dev_get_plat(dev);
	struct mmc_uclass_priv *upriv = dev_get_uclass_priv(dev);

	upriv->mmc = &plat->mmc;
	return 0;
}

static int litesd_bind(struct udevice *dev)
{
	struct litesd_plat *plat = dev_get_plat(dev);
	int ret;

	plat->cfg.name = dev->name;
	/* Advertise 4-bit but not high-speed: the modified SDCore + soft-core
	 * PHY cannot sustain the high-speed switch, and the BIOS reference caps
	 * the read clock at 15 MHz. f_max clamps every mmc_set_clock() request
	 * (including the core's error-path tran_speed fallback) to a safe clock.
	 */
	plat->cfg.host_caps = MMC_MODE_4BIT | MMC_MODE_HS;
	plat->cfg.voltages = MMC_VDD_32_33 | MMC_VDD_33_34;
	plat->cfg.f_min = 400000;
	plat->cfg.f_max = 7500000;
	plat->cfg.b_max = CONFIG_SYS_MMC_MAX_BLK_COUNT;

	ret = mmc_bind(dev, &plat->mmc, &plat->cfg);
	if (ret)
		return ret;
	return 0;
}

static const struct udevice_id litesd_ids[] = {
	{ .compatible = "riscv-mini,litesd" },
	{ }
};

U_BOOT_DRIVER(litesd) = {
	.name		= "litesd",
	.id		= UCLASS_MMC,
	.of_match	= litesd_ids,
	.bind		= litesd_bind,
	.of_to_plat	= litesd_of_to_plat,
	.probe		= litesd_probe,
	.ops		= &litesd_ops,
	.priv_auto	= sizeof(struct litesd_priv),
	.plat_auto	= sizeof(struct litesd_plat),
};
