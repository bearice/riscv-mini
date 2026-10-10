// SPDX-License-Identifier: GPL-2.0
/* Modified LiteSDCard native 32-bit CSRs, bounded coherent bounce DMA. */
#include <linux/delay.h>
#include <linux/dma-mapping.h>
#include <linux/iopoll.h>
#include <linux/mmc/host.h>
#include <linux/module.h>
#include <linux/platform_device.h>
#include <linux/scatterlist.h>
#include <linux/workqueue.h>

enum { CD=0, DIV=4, INIT=8, CMD_TO=12, DATA_TO=20, SETTINGS=24,
	ARG=28, CMD=32, SEND=36, RESP=40, CMD_EVENT=56, DATA_EVENT=60,
	BLK_LEN=64, BLK_CNT=68, RX_BASE=72, RX_LEN=76, RX_EN=80,
	RX_DONE=84, RX_ERR=88, RX_BUSY=96, TX_BASE=100, TX_LEN=104,
	TX_EN=108, TX_DONE=112, TX_ERR=116, TX_BUSY=124,
	EV_PENDING=132, EV_ENABLE=136 };
#define MINI_SD_BYTES 4096
struct mini_sd {
	struct mmc_host *host;
	void __iomem *regs, *reset;
	void *bounce;
	dma_addr_t dma;
	struct work_struct work;
	struct mmc_request *mrq;
	bool dead;
};
static int mini_sd_stop(struct mini_sd *s)
{
	u32 busy;
	writel(0, s->regs + RX_EN); writel(0, s->regs + TX_EN);
	return read_poll_timeout(readl, busy, !busy, 10, 100000, false,
		s->regs + RX_BUSY) ?: read_poll_timeout(readl, busy, !busy,
		10, 100000, false, s->regs + TX_BUSY);
}
static int mini_sd_event(struct mini_sd *s, unsigned int offset)
{
	u32 event;
	int ret = readl_poll_timeout(s->regs + offset, event,
		(event & 1) || (readl(s->regs + CD) & 1), 10, 1000000);
	if (readl(s->regs + CD) & 1) return -ENOMEDIUM;
	if (ret) return ret;
	return event & 2 ? -ETIMEDOUT : event & 12 ? -EILSEQ : 0;
}
static void mini_sd_command(struct mini_sd *s, struct mmc_command *cmd,
			    struct mmc_data *data)
{
	u32 flags = cmd->flags & MMC_RSP_136 ? 2 :
		cmd->flags & MMC_RSP_BUSY ? 3 : cmd->flags & MMC_RSP_PRESENT ? 1 : 0;
	unsigned int bytes = 0;
	bool write = data && (data->flags & MMC_DATA_WRITE);
	int ret, i;
	u32 done;
	if (s->dead) { cmd->error = -EIO; return; }
	if (readl(s->regs + CD) & 1) { cmd->error = -ENOMEDIUM; return; }
	if (cmd->flags & MMC_RSP_CRC) flags |= 4;
	if (data) {
		bytes = data->blksz * data->blocks;
		if (!bytes || bytes > MINI_SD_BYTES || (bytes & 3)) {
			cmd->error = -EINVAL; data->error = -EINVAL; return;
		}
		if ((flags & 3) == 3) flags = (flags & ~3) | 1;
		flags |= write ? 64 : 32;
		writel(data->blksz, s->regs + BLK_LEN);
		writel(data->blocks, s->regs + BLK_CNT);
		if (write && sg_copy_to_buffer(data->sg, data->sg_len, s->bounce, bytes) != bytes) {
			cmd->error = -EINVAL; data->error = -EINVAL; return;
		}
		dma_wmb();
		writel(0, s->regs + (write ? TX_EN : RX_EN));
		writel(s->dma, s->regs + (write ? TX_BASE : RX_BASE));
		writel(bytes, s->regs + (write ? TX_LEN : RX_LEN));
		writel(1, s->regs + (write ? TX_EN : RX_EN));
	}
	writel(cmd->arg, s->regs + ARG);
	writel((cmd->opcode << 8) | flags, s->regs + CMD);
	writel(1, s->regs + SEND);
	cmd->error = mini_sd_event(s, CMD_EVENT);
	if (!cmd->error) {
		if (cmd->flags & MMC_RSP_136)
			for (i=0; i<4; i++) cmd->resp[i] = readl(s->regs + RESP + 4*i);
		else if (cmd->flags & MMC_RSP_PRESENT)
			cmd->resp[0] = readl(s->regs + RESP + 12);
	}
	if (data) {
		ret = cmd->error ?: mini_sd_event(s, DATA_EVENT);
		if (!ret) ret = readl_poll_timeout(s->regs + (write ? TX_DONE : RX_DONE),
			done, done || readl(s->regs + (write ? TX_ERR : RX_ERR)), 10, 1000000);
		if (!ret && readl(s->regs + (write ? TX_ERR : RX_ERR))) ret = -EIO;
		if (mini_sd_stop(s)) { s->dead = true; ret = -ETIMEDOUT; }
		data->error = ret;
		if (!ret) {
			/* Device writes shared L2; invalidate the private write-through L1. */
			if (!write) {
				asm volatile("fence rw,rw\n.word 0x0000500f" ::: "memory");
				if (sg_copy_from_buffer(data->sg, data->sg_len, s->bounce, bytes) != bytes)
					data->error = -EIO;
			}
			if (!data->error) data->bytes_xfered = bytes;
		}
	}
	if (!cmd->error && (cmd->flags & MMC_RSP_BUSY)) msleep(10);
}
static void mini_sd_work(struct work_struct *work)
{
	struct mini_sd *s = container_of(work, struct mini_sd, work);
	struct mmc_request *mrq = s->mrq;
	if (mrq->sbc) mini_sd_command(s, mrq->sbc, NULL);
	if (!mrq->sbc || !mrq->sbc->error) {
		mini_sd_command(s, mrq->cmd, mrq->data);
		if (mrq->stop) mini_sd_command(s, mrq->stop, NULL);
	}
	s->mrq = NULL;
	mmc_request_done(s->host, mrq);
}
static void mini_sd_request(struct mmc_host *host, struct mmc_request *mrq)
{
	struct mini_sd *s = mmc_priv(host);
	s->mrq = mrq; schedule_work(&s->work);
}
static void mini_sd_ios(struct mmc_host *host, struct mmc_ios *ios)
{
	struct mini_sd *s = mmc_priv(host);
	u32 clock = clamp(ios->clock, 400000u, 7500000u);
	writel(DIV_ROUND_UP(60000000u, clock), s->regs + DIV);
	writel(ios->bus_width == MMC_BUS_WIDTH_4, s->regs + SETTINGS);
	if (ios->power_mode == MMC_POWER_UP) { writel(1, s->regs + INIT); msleep(2); }
}
static int mini_sd_cd(struct mmc_host *host)
{ return !(readl(((struct mini_sd *)mmc_priv(host))->regs + CD) & 1); }
static const struct mmc_host_ops mini_sd_ops = {
	.request = mini_sd_request, .set_ios = mini_sd_ios, .get_cd = mini_sd_cd,
};
static int mini_sd_probe(struct platform_device *pdev)
{
	struct mmc_host *host;
	struct mini_sd *s;
	int ret;
	ret = dma_set_mask_and_coherent(&pdev->dev, DMA_BIT_MASK(32));
	if (ret) return ret;
	host = mmc_alloc_host(sizeof(*s), &pdev->dev);
	if (!host) return -ENOMEM;
	s = mmc_priv(host); s->host = host;
	s->regs = devm_platform_ioremap_resource(pdev, 0);
	s->reset = devm_platform_ioremap_resource(pdev, 1);
	if (IS_ERR(s->regs) || IS_ERR(s->reset)) {
		ret = IS_ERR(s->regs) ? PTR_ERR(s->regs) : PTR_ERR(s->reset); goto free;
	}
	ret = mini_sd_stop(s); if (ret) goto free;
	s->bounce = dma_alloc_coherent(&pdev->dev, MINI_SD_BYTES, &s->dma, GFP_KERNEL);
	if (!s->bounce) { ret = -ENOMEM; goto free; }
	if (!s->dma || s->dma + MINI_SD_BYTES > 0x07e00000) { ret=-ERANGE; goto dma; }
	writel(1, s->reset); msleep(1); writel(0, s->reset); msleep(1);
	writel(0, s->regs + EV_ENABLE); writel(15, s->regs + EV_PENDING);
	writel(150, s->regs + DIV); writel(0, s->regs + SETTINGS);
	writel(15000000, s->regs + CMD_TO); writel(30000000, s->regs + DATA_TO);
	INIT_WORK(&s->work, mini_sd_work);
	host->ops = &mini_sd_ops; host->f_min = 400000; host->f_max = 7500000;
	host->ocr_avail = MMC_VDD_32_33 | MMC_VDD_33_34;
	host->caps = MMC_CAP_4_BIT_DATA | MMC_CAP_NEEDS_POLL;
	host->caps2 = MMC_CAP2_NO_WRITE_PROTECT;
	host->max_blk_size = 512; host->max_blk_count = 8;
	host->max_req_size = MINI_SD_BYTES; host->max_seg_size = MINI_SD_BYTES;
	host->max_segs = 128;
	platform_set_drvdata(pdev, s);
	ret = mmc_add_host(host); if (!ret) return 0;
dma:
	dma_free_coherent(&pdev->dev, MINI_SD_BYTES, s->bounce, s->dma);
free:
	mmc_free_host(host); return ret;
}
static void mini_sd_shutdown(struct platform_device *pdev)
{
	struct mini_sd *s = platform_get_drvdata(pdev);
	mmc_remove_host(s->host); cancel_work_sync(&s->work);
	if (mini_sd_stop(s)) dev_err(&pdev->dev, "DMA drain failed; buffer retained\n");
}
static const struct of_device_id mini_sd_match[] = {
	{ .compatible = "riscv-mini,litesd" }, {}
};
static struct platform_driver mini_sd_driver = {
	.probe = mini_sd_probe, .shutdown = mini_sd_shutdown,
	.driver = { .name = "mini-mmc", .of_match_table = mini_sd_match,
		.suppress_bind_attrs = true },
};
builtin_platform_driver(mini_sd_driver);
