// SPDX-License-Identifier: GPL-2.0-only
/* Fixed 480x272 RGB565 fbdev for the coherent riscv-mini LCD DMA port. */
#include <linux/dma-mapping.h>
#include <linux/dma-map-ops.h>
#include <linux/fb.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>

#define MINI_WIDTH 480
#define MINI_HEIGHT 272
#define MINI_BYTES (MINI_WIDTH * MINI_HEIGHT * 2)
#define MINI_BUSY BIT(1)
enum mini_reg { ENABLE, SELECT, BASE0, BASE1, STATE, ADDRESS_ERROR,
                FRAMES, COMPLETED, UNDERFLOWS, REG_COUNT };

struct mini_fb {
    void __iomem *csr;
    u32 offsets[REG_COUNT];
    u32 palette[16];
    dma_addr_t dma;
    struct fb_info *info;
};

static void __iomem *mini_reg(struct mini_fb *fb, enum mini_reg reg)
{
    return fb->csr + fb->offsets[reg];
}

static int mini_stop(struct mini_fb *fb)
{
    u32 state;
    writel(0, mini_reg(fb, ENABLE));
    /* Drain the CDC and final scan/DMA transaction before releasing memory. */
    usleep_range(1000, 2000);
    return readl_poll_timeout(mini_reg(fb, STATE), state,
                             !(state & MINI_BUSY), 1000, 100000);
}

static int mini_blank(int blank, struct fb_info *info)
{
    struct mini_fb *fb = info->par;
    if (blank != FB_BLANK_UNBLANK)
        return mini_stop(fb);
    writel(1, mini_reg(fb, ENABLE));
    return 0;
}

static int mini_check_var(struct fb_var_screeninfo *var, struct fb_info *info)
{
    if (var->xres != MINI_WIDTH || var->yres != MINI_HEIGHT ||
        var->xres_virtual != MINI_WIDTH || var->yres_virtual != MINI_HEIGHT ||
        var->bits_per_pixel != 16 || var->xoffset || var->yoffset ||
        var->grayscale || var->nonstd)
        return -EINVAL;
    var->red = info->var.red;
    var->green = info->var.green;
    var->blue = info->var.blue;
    var->transp = info->var.transp;
    return 0;
}

static int mini_setcolreg(unsigned regno, unsigned red, unsigned green,
                          unsigned blue, unsigned transp, struct fb_info *info)
{
    struct mini_fb *fb = info->par;
    if (regno >= ARRAY_SIZE(fb->palette))
        return -EINVAL;
    fb->palette[regno] = ((red >> 11) << 11) | ((green >> 10) << 5) | (blue >> 11);
    return 0;
}

static int mini_mmap(struct fb_info *info, struct vm_area_struct *vma)
{
    struct mini_fb *fb = info->par;
    return dma_mmap_coherent(info->device, vma, info->screen_buffer, fb->dma, MINI_BYTES);
}

static const struct fb_ops mini_ops = {
    .owner = THIS_MODULE,
    __FB_DEFAULT_DMAMEM_OPS_RDWR,
    __FB_DEFAULT_DMAMEM_OPS_DRAW,
    .fb_mmap = mini_mmap,
    .fb_check_var = mini_check_var,
    .fb_setcolreg = mini_setcolreg,
    .fb_blank = mini_blank,
};

static ssize_t dma_status_show(struct device *dev, struct device_attribute *attr, char *buf)
{
    struct fb_info *info = dev_get_drvdata(dev);
    struct mini_fb *fb = info->par;
    /* Individual CSR snapshots, not an atomic snapshot of the running DMA. */
    return sysfs_emit(buf, "enable=%u base=%08x frames=%u completed=%u underflows=%u address_error=%u state=%u\n",
        readl(mini_reg(fb, ENABLE)), readl(mini_reg(fb, BASE0)),
        readl(mini_reg(fb, FRAMES)), readl(mini_reg(fb, COMPLETED)),
        readl(mini_reg(fb, UNDERFLOWS)), readl(mini_reg(fb, ADDRESS_ERROR)),
        readl(mini_reg(fb, STATE)));
}
static DEVICE_ATTR_RO(dma_status);

static int mini_probe(struct platform_device *pdev)
{
    struct device *dev = &pdev->dev;
    struct resource *res;
    struct fb_info *info;
    struct mini_fb *fb;
    int ret, i;

    /* CPU L1 writes through; all LCD DMA reads share the writeback L2.
     * Do not claim this binding on a noncoherent gateware configuration. */
    if (!dev_is_dma_coherent(dev))
        return dev_err_probe(dev, -EINVAL, "LCD requires coherent DMA binding\n");
    ret = dma_set_mask_and_coherent(dev, DMA_BIT_MASK(32));
    if (ret)
        return ret;
    info = framebuffer_alloc(sizeof(*fb), dev);
    if (!info)
        return -ENOMEM;
    fb = info->par;
    fb->info = info;
    res = platform_get_resource(pdev, IORESOURCE_MEM, 0);
    fb->csr = devm_ioremap_resource(dev, res);
    if (IS_ERR(fb->csr)) { ret = PTR_ERR(fb->csr); goto release; }
    ret = of_property_read_u32_array(dev->of_node, "riscv-mini,linux-csr-offsets",
                                     fb->offsets, REG_COUNT);
    if (ret)
        goto release;
    for (i = 0; i < REG_COUNT; ++i)
        if ((fb->offsets[i] & 3) || resource_size(res) < 4 ||
            fb->offsets[i] > resource_size(res) - 4) { ret = -EINVAL; goto release; }
    ret = mini_stop(fb);
    if (ret)
        goto release;
    info->screen_buffer = dma_alloc_coherent(dev, MINI_BYTES, &fb->dma, GFP_KERNEL);
    if (!info->screen_buffer) { ret = -ENOMEM; goto release; }
    if (fb->dma < 4096 || (fb->dma & 15) || fb->dma > 0x08000000 - MINI_BYTES) {
        ret = -EINVAL; goto free;
    }
    memset(info->screen_buffer, 0, MINI_BYTES);
    info->screen_size = MINI_BYTES;
    info->flags = FBINFO_VIRTFB;
    info->fbops = &mini_ops;
    info->pseudo_palette = fb->palette;
    strscpy(info->fix.id, "riscv-mini", sizeof(info->fix.id));
    info->fix.smem_start = fb->dma;
    info->fix.smem_len = MINI_BYTES;
    info->fix.type = FB_TYPE_PACKED_PIXELS;
    info->fix.visual = FB_VISUAL_TRUECOLOR;
    info->fix.line_length = MINI_WIDTH * 2;
    info->var.xres = info->var.xres_virtual = MINI_WIDTH;
    info->var.yres = info->var.yres_virtual = MINI_HEIGHT;
    info->var.bits_per_pixel = 16;
    info->var.red = (struct fb_bitfield){11, 5, 0};
    info->var.green = (struct fb_bitfield){5, 6, 0};
    info->var.blue = (struct fb_bitfield){0, 5, 0};
    info->var.width = info->var.height = -1;
    writel(fb->dma, mini_reg(fb, BASE0));
    writel(fb->dma, mini_reg(fb, BASE1));
    writel(0, mini_reg(fb, SELECT));
    writel(1, mini_reg(fb, ENABLE));
    msleep(40);
    if (readl(mini_reg(fb, ADDRESS_ERROR))) { ret = -EIO; goto stop; }
    platform_set_drvdata(pdev, info);
    ret = register_framebuffer(info);
    if (ret)
        goto stop;
    ret = device_create_file(dev, &dev_attr_dma_status);
    if (ret) { unregister_framebuffer(info); goto stop; }
    dev_info(dev, "fb%d: 480x272 RGB565 coherent DMA at %08x\n", info->node, (u32)fb->dma);
    return 0;

stop:
    /* Never free a buffer if scanout failed to drain: quarantine it. */
    if (mini_stop(fb)) {
        dev_err(dev, "LCD DMA did not drain; retaining framebuffer memory\n");
        goto release;
    }
free:
    dma_free_coherent(dev, MINI_BYTES, info->screen_buffer, fb->dma);
release:
    framebuffer_release(info);
    return ret;
}

static void mini_shutdown(struct platform_device *pdev)
{
    struct fb_info *info = platform_get_drvdata(pdev);
    if (info && mini_stop(info->par))
        dev_err(&pdev->dev, "LCD DMA drain timed out at shutdown\n");
}

static const struct of_device_id mini_of_match[] = {
    { .compatible = "riscv-mini,rgb-lcd" }, { }
};
MODULE_DEVICE_TABLE(of, mini_of_match);
/* Built-in only: mappings and scanout remain owned until kernel shutdown. */
static struct platform_driver mini_driver = {
    .probe = mini_probe,
    .shutdown = mini_shutdown,
    .driver = { .name = "riscv-mini-fb", .of_match_table = mini_of_match,
                .suppress_bind_attrs = true },
};
module_platform_driver(mini_driver);
MODULE_LICENSE("GPL");
