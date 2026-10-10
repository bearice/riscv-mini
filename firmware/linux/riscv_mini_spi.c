// SPDX-License-Identifier: GPL-2.0
/* LiteX SPI master: native single-word, mode zero, software-held CS. */
#include <linux/iopoll.h>
#include <linux/module.h>
#include <linux/platform_device.h>
#include <linux/spi/spi.h>
struct mini_spi { void __iomem *regs; };
static void mini_spi_cs(struct spi_device *spi,bool inactive)
{
	struct mini_spi *p=spi_controller_get_devdata(spi->controller);
	writel(0x10000|!inactive,p->regs+16);
}
static int mini_spi_transfer(struct spi_controller *ctl,struct spi_device *spi,struct spi_transfer *xfer)
{
	struct mini_spi *p=spi_controller_get_devdata(ctl);
	const u8 *tx=xfer->tx_buf; u8 *rx=xfer->rx_buf;
	u32 status; unsigned int i; int ret;
	writel(DIV_ROUND_UP(60000000u,xfer->speed_hz),p->regs+20);
	for(i=0;i<xfer->len;) {
		unsigned int bytes=min(2u,xfer->len-i);
		u32 value=tx?tx[i]:0xff;
		if(bytes==2) value=(value<<8)|(tx?tx[i+1]:0xff);
		/* In-order uncached CSR transactions need no per-access memory fence.
		 * The ordered divider write above publishes all preceding memory. */
		writel_relaxed(value,p->regs+8); writel_relaxed((bytes*8<<8)|1,p->regs);
		/* One byte takes ~1 us; scheduling a sleep here would turn a full
		 * frame into tens of thousands of scheduler wakeups. */
		/* Busy-poll without udelay: emulated rdtime makes a one-us delay
		 * vastly more expensive than this short uncached CSR transaction. */
		ret=read_poll_timeout_atomic(readl_relaxed,status,status&5,0,10000,false,p->regs+4);
		if(ret || (status&4)) return ret?:-EIO;
		if(rx) {
			value=readl_relaxed(p->regs+12);
			rx[i]=bytes==2?value>>8:value;
			if(bytes==2) rx[i+1]=value;
		}
		i+=bytes;
		if(!(i&255)) cond_resched();
	}
	return 0;
}
static int mini_spi_probe(struct platform_device *pdev)
{
	struct spi_controller *ctl=devm_spi_alloc_host(&pdev->dev,sizeof(struct mini_spi));
	struct mini_spi *p;
	if(!ctl) return -ENOMEM;
	p=spi_controller_get_devdata(ctl); p->regs=devm_platform_ioremap_resource(pdev,0);
	if(IS_ERR(p->regs)) return PTR_ERR(p->regs);
	ctl->dev.of_node=pdev->dev.of_node; ctl->num_chipselect=1; ctl->bus_num=-1;
	ctl->bits_per_word_mask=SPI_BPW_MASK(8); ctl->max_speed_hz=6000000;
	ctl->min_speed_hz=400000; ctl->set_cs=mini_spi_cs; ctl->transfer_one=mini_spi_transfer;
	writel(0x10000,p->regs+16);
	return devm_spi_register_controller(&pdev->dev,ctl);
}
static const struct of_device_id mini_spi_match[]={ {.compatible="riscv-mini,litespi32"},{} };
static struct platform_driver mini_spi_driver={.probe=mini_spi_probe,
	.driver={.name="mini-spi",.of_match_table=mini_spi_match,.suppress_bind_attrs=true}};
builtin_platform_driver(mini_spi_driver);
