// SPDX-License-Identifier: GPL-2.0+
/* 480x272 RGB565 scanout through the riscv-mini coherent LCD DMA port. */
#include <dm.h>
#include <mapmem.h>
#include <video.h>
#include <asm/io.h>
#include <linux/delay.h>
#include <linux/iopoll.h>

#define LCD_WIDTH 480
#define LCD_HEIGHT 272
#define LCD_FRAME_BYTES (LCD_WIDTH * LCD_HEIGHT * 2)
#define LCD_BUSY BIT(1)

/* Offsets come from the selected SoC's csr.json, not a historical CSR map. */
enum lcd_reg { ENABLE, SELECT, BASE0, BASE1, STATE, ADDRESS_ERROR, REG_COUNT };

struct mini_lcd_priv {
	void __iomem *csr;
	u32 offsets[REG_COUNT];
};

static void __iomem *lcd_reg(struct mini_lcd_priv *priv, enum lcd_reg reg)
{
	return priv->csr + priv->offsets[reg];
}

static int mini_lcd_stop(struct udevice *dev)
{
	struct mini_lcd_priv *priv = dev_get_priv(dev);
	u32 state;

	writel(0, lcd_reg(priv, ENABLE));
	asm volatile("fence rw,rw" ::: "memory");
	/* Let the pixel-domain disable cross CDC and drain the current frame. */
	udelay(1000);
	return readl_poll_timeout(lcd_reg(priv, STATE), state,
				 !(state & LCD_BUSY), 100000);
}

static int mini_lcd_bind(struct udevice *dev)
{
	struct video_uc_plat *plat = dev_get_uclass_plat(dev);

	plat->size = LCD_FRAME_BYTES;
	plat->align = 16;
	return 0;
}

static int mini_lcd_probe(struct udevice *dev)
{
	struct mini_lcd_priv *priv = dev_get_priv(dev);
	struct video_uc_plat *plat = dev_get_uclass_plat(dev);
	struct video_priv *video = dev_get_uclass_priv(dev);
	fdt_size_t size;
	fdt_addr_t addr;
	int ret, i;

	addr = dev_read_addr_size(dev, &size);
	if (addr == FDT_ADDR_T_NONE)
		return -EINVAL;
	ret = dev_read_u32_array(dev, "riscv-mini,csr-offsets", priv->offsets,
				 REG_COUNT);
	if (ret)
		return ret;
	for (i = 0; i < REG_COUNT; i++)
		if ((priv->offsets[i] & 3) || size < 4 ||
		    priv->offsets[i] > size - 4)
			return -EINVAL;
	priv->csr = map_sysmem(addr, size);
	if (plat->base < 4096 || (plat->base & 15) ||
	    plat->base > 0x08000000 - LCD_FRAME_BYTES)
		return -EINVAL;
	ret = mini_lcd_stop(dev);
	if (ret)
		return ret;

	video->xsize = LCD_WIDTH;
	video->ysize = LCD_HEIGHT;
	video->bpix = VIDEO_BPP16;
	video->line_length = LCD_WIDTH * 2;
	/* CPU L1 is write-through; the shared writeback L2 snoops LCD DMA. */
	video_set_flush_dcache(dev, false);
	memset(map_sysmem(plat->base, plat->size), 0, plat->size);
	writel(plat->base, lcd_reg(priv, BASE0));
	writel(plat->base, lcd_reg(priv, BASE1));
	writel(0, lcd_reg(priv, SELECT));
	asm volatile("fence rw,rw" ::: "memory");
	writel(1, lcd_reg(priv, ENABLE));
	udelay(40000);
	if (readl(lcd_reg(priv, ADDRESS_ERROR))) {
		mini_lcd_stop(dev);
		return -EIO;
	}
	printf("RGB LCD: 480x272 RGB565 framebuffer at %08lx\n", plat->base);
	return 0;
}

static int mini_lcd_sync(struct udevice *dev)
{
	/* Order text/bitmap stores before the coherent DMA reads them. */
	asm volatile("fence rw,rw" ::: "memory");
	return 0;
}

static const struct video_ops mini_lcd_ops = {
	.video_sync = mini_lcd_sync,
};

static const struct udevice_id mini_lcd_ids[] = {
	{ .compatible = "riscv-mini,rgb-lcd" },
	{ }
};

U_BOOT_DRIVER(riscv_mini_lcd) = {
	.name = "riscv_mini_lcd",
	.id = UCLASS_VIDEO,
	.of_match = mini_lcd_ids,
	.bind = mini_lcd_bind,
	.probe = mini_lcd_probe,
	.remove = mini_lcd_stop,
	/* Linux may reuse U-Boot's framebuffer RAM: drain DMA before entry. */
	.flags = DM_FLAG_OS_PREPARE,
	.ops = &mini_lcd_ops,
	.priv_auto = sizeof(struct mini_lcd_priv),
};
