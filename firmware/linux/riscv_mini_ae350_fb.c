// SPDX-License-Identifier: GPL-2.0-only
/*
 * ae350_video fbdev driver for the Tang Mega 138K Pro (Andes AE350 SoC).
 *
 * Drives the AE350 video scanout engine (fixed modes + programmable custom
 * modes in RGB565/XRGB1555) with double-buffered frame present, and uses
 * the GFX2D accelerator for fillrect/copyarea/imageblit (fbcon text).
 */
#include <linux/atomic.h>
#include <linux/dma-mapping.h>
#include <linux/dma-map-ops.h>
#include <linux/fb.h>
#include <linux/io.h>
#include <linux/log2.h>
#include <linux/math64.h>
#include <linux/miscdevice.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/slab.h>
#include <linux/spinlock.h>
#include <linux/uaccess.h>

#include "riscv_mini_ae350_regs.h"
#include "ae350_gfx3d_uapi.h"

struct ae350_fb {
	void __iomem *csr;	/* video engine */
	void __iomem *gfx;	/* gfx2d accelerator (may be NULL) */
	struct fb_info *info;
	u32 format;		/* AE350_FMT_* */
	u32 bpp;		/* bytes per pixel */
	u32 mode_index;
	u32 width;
	u32 height;
	u32 stride;		/* bytes per line */
	u32 frame_bytes;
	dma_addr_t dma[2];
	void *cpu[2];
	u32 active_slot;
	bool blanked;
	u32 palette[16];
	/* Blit bitmap staging: the GFX2D engine reads DDR only, so fbcon
	 * bitmaps (kernel memory) are copied here before IMAGE_BLIT. */
	u8 *scratch;
	dma_addr_t scratch_dma;
	size_t scratch_size;
	spinlock_t gfx_lock;
	/* GFX3D offscreen render target + Z-buffer exposed via /dev/ae350-gfx3d. */
	struct mutex gfx3d_lock;
	atomic_t gfx3d_active;
	struct miscdevice gfx3d_misc;
	bool gfx3d_registered;
	void *gfx3d_color_cpu;
	dma_addr_t gfx3d_color_dma;
	size_t gfx3d_color_bytes;
	void *gfx3d_z_cpu;
	dma_addr_t gfx3d_z_dma;
	size_t gfx3d_z_bytes;
	u32 gfx3d_stride_px;
};

static void ae350_fb_clean(struct ae350_fb *fb, u32 slot)
{
	/* VA-type cache command: pass the CPU virtual address, not the
	 * physical dma handle. */
	ae350_cache_clean_range((u32)(uintptr_t)fb->cpu[slot], fb->frame_bytes);
}

static int ae350_present(struct ae350_fb *fb, u32 slot)
{
	u32 status = readl(fb->csr + AE350_V_STATUS);
	int ret;

	ae350_fb_clean(fb, slot);
	/* If this slot is already active, nothing to do. */
	if ((status & 1u) && ((status >> 1) & 1u) == slot)
		return 0;
	if (!(readl(fb->csr + 8) & 0x40u)) /* CAP bit6: runtime output */
		return 0;
	if (!readl(fb->csr + AE350_V_ENABLE)) {
		ret = ae350_video_enable(fb->csr, 1);
		if (ret)
			return ret;
	}
	ret = ae350_video_commit(fb->csr, 1u | (slot << 1));
	if (ret)
		return ret;
	status = readl(fb->csr + AE350_V_STATUS);
	return ((status & 3u) == (1u | (slot << 1)) &&
		readl(fb->csr + AE350_V_ACTIVE_BASE) == fb->dma[slot]) ?
		0 : -EIO;
}

static int ae350_fb_check_var(struct fb_var_screeninfo *var,
			      struct fb_info *info)
{
	struct ae350_fb *fb = info->par;

	if (!var->xres_virtual)
		var->xres_virtual = fb->width;
	if (!var->yres_virtual || var->yres_virtual == fb->height)
		var->yres_virtual = fb->height * 2;
	if (var->xres != fb->width || var->yres != fb->height ||
	    var->xres_virtual != fb->width ||
	    var->yres_virtual != fb->height * 2 ||
	    var->bits_per_pixel != fb->bpp * 8 || var->xoffset ||
	    (var->yoffset != 0 && var->yoffset != fb->height) ||
	    var->grayscale || var->nonstd)
		return -EINVAL;
	var->red = info->var.red;
	var->green = info->var.green;
	var->blue = info->var.blue;
	var->transp = info->var.transp;
	return 0;
}

static int ae350_fb_set_par(struct fb_info *info)
{
	struct ae350_fb *fb = info->par;
	u32 slot = info->var.yoffset ? 1 : 0;
	int ret;

	ret = ae350_present(fb, slot);
	if (!ret)
		fb->active_slot = slot;
	return ret;
}

static int ae350_fb_setcolreg(unsigned regno, unsigned red, unsigned green,
			      unsigned blue, unsigned transp,
			      struct fb_info *info)
{
	struct ae350_fb *fb = info->par;

	if (fb->format == AE350_FMT_INDEX8) {
		if (regno >= 256)
			return -EINVAL;
		u16 val = ((red >> 11) << 11) | ((green >> 10) << 5) | (blue >> 11);
		writel(regno, fb->csr + AE350_V_PALETTE_ADDR);
		writel(val, fb->csr + AE350_V_PALETTE_DATA);
		if (regno < 16)
			fb->palette[regno] = regno;
		return 0;
	}

	if (regno >= 16)
		return -EINVAL;
	if (fb->format == AE350_FMT_XRGB8888 || fb->format == AE350_FMT_RGB888)
		fb->palette[regno] =
			((red >> 8) << 16) | ((green >> 8) << 8) | (blue >> 8);
	else if (fb->format == AE350_FMT_XRGB1555)
		fb->palette[regno] =
			((red >> 11) << 10) | ((green >> 11) << 5) | (blue >> 11);
	else
		fb->palette[regno] =
			((red >> 11) << 11) | ((green >> 10) << 5) | (blue >> 11);
	return 0;
}

static int ae350_fb_blank(int blank, struct fb_info *info)
{
	struct ae350_fb *fb = info->par;
	int ret;

	if (blank != FB_BLANK_UNBLANK) {
		int ret = ae350_video_enable(fb->csr, 0);

		if (ret)
			return -EIO;
		fb->blanked = true;
		return 0;
	}
	ret = ae350_present(fb, fb->active_slot);
	if (!ret)
		fb->blanked = false;
	return ret;
}

static int ae350_fb_mmap(struct fb_info *info, struct vm_area_struct *vma)
{
	struct ae350_fb *fb = info->par;

	return dma_mmap_coherent(info->device, vma, fb->cpu[0], fb->dma[0],
				 fb->frame_bytes * 2);
}

static int ae350_fb_pan_display(struct fb_var_screeninfo *var,
				struct fb_info *info)
{
	struct ae350_fb *fb = info->par;
	u32 slot;
	int ret;

	if (var->xoffset != 0 || (var->yoffset != 0 && var->yoffset != fb->height))
		return -EINVAL;
	slot = var->yoffset ? 1 : 0;
	ret = ae350_present(fb, slot);
	if (!ret)
		fb->active_slot = slot;
	return ret;
}

/*
 * GFX2D-backed accel.  fbcon passes coordinates in the *virtual* screen
 * space (they include xoffset/yoffset and can span both buffers), so the
 * slot is derived from the coordinate itself: slot = y / height.
 */

static u32 ae350_phys(struct ae350_fb *fb, u32 x, u32 y)
{
	u32 slot = y >= fb->height ? 1 : 0;

	return (u32)fb->dma[slot] + (x + (y - slot * fb->height) * fb->width) * fb->bpp;
}

static void *ae350_cpu_addr(struct ae350_fb *fb, u32 x, u32 y)
{
	u32 slot = y >= fb->height ? 1 : 0;

	return (u8 *)fb->cpu[slot] + (x + (y - slot * fb->height) * fb->width) * fb->bpp;
}

static void ae350_fb_cctl_rect(struct ae350_fb *fb, u32 x, u32 y,
			       u32 width, u32 height, u32 cmd)
{
	u32 r, bytes;

	if (!width || !height || x >= fb->width || y >= fb->height * 2)
		return;
	if (x + width > fb->width)
		width = fb->width - x;
	if (y + height > fb->height * 2)
		height = fb->height * 2 - y;
	bytes = width * fb->bpp;

	for (r = 0; r < height; r++) {
		u32 addr = (u32)(uintptr_t)ae350_cpu_addr(fb, x, y + r);
		u32 end = addr + bytes;

		addr &= ~31u;
		while (addr < end) {
			asm volatile("csrw 0x80b, %0" :: "r"(addr));
			asm volatile("csrw 0x80c, %0" :: "r"(cmd));
			addr += 32u;
		}
	}
	asm volatile("fence" ::: "memory");
}

static inline void ae350_fb_clean_rect(struct ae350_fb *fb, u32 x, u32 y,
				       u32 width, u32 height)
{
	ae350_fb_cctl_rect(fb, x, y, width, height, 1u);
}

static inline void ae350_fb_inval_rect(struct ae350_fb *fb, u32 x, u32 y,
				       u32 width, u32 height)
{
	ae350_fb_cctl_rect(fb, x, y, width, height, 0u);
}

static void ae350_gfx_write_desc(struct ae350_fb *fb, u32 op,
				 u32 src, u32 dst, u32 src_stride, u32 dst_stride,
				 u32 width, u32 height, u32 fg, u32 bg, u32 wb)
{
	void __iomem *g = fb->gfx;

	writel(op, g + AE350_G_OP);
	writel(src, g + AE350_G_SRC_ADDR);
	writel(dst, g + AE350_G_DST_ADDR);
	writel(src_stride, g + AE350_G_SRC_STRIDE);
	writel(dst_stride, g + AE350_G_DST_STRIDE);
	writel(width, g + AE350_G_WIDTH);
	writel(height, g + AE350_G_HEIGHT);
	writel(fg, g + AE350_G_FG_COLOR);
	writel(bg, g + AE350_G_BG_COLOR);
	writel(wb, g + AE350_G_BLIT_SRC_WB);
	writel(fb->format, g + AE350_G_FORMAT);
}

static int ae350_gfx_ready(struct ae350_fb *fb)
{
	return fb->gfx && !atomic_read(&fb->gfx3d_active) &&
		(fb->format <= AE350_FMT_XRGB1555) &&
		readl(fb->gfx) == AE350_GFX2D_ID &&
		!(readl(fb->gfx + AE350_G_STATUS) & AE350_G_STATUS_BUSY);
}

static void ae350_fb_fillrect(struct fb_info *info,
			      const struct fb_fillrect *rect)
{
	struct ae350_fb *fb = info->par;
	u32 dst;
	u32 color;

	if (rect->rop == ROP_COPY && fb->gfx &&
	    rect->width && rect->height &&
	    rect->dy + rect->height <=
		(rect->dy >= fb->height ? fb->height * 2 : fb->height)) {
		unsigned long flags;
		int rc = -EBUSY;

		dst = ae350_phys(fb, rect->dx, rect->dy);
		color = rect->color < 16 ? ((u32 *)info->pseudo_palette)[rect->color] :
					   rect->color;
		spin_lock_irqsave(&fb->gfx_lock, flags);
		if (ae350_gfx_ready(fb)) {
			ae350_fb_clean_rect(fb, rect->dx, rect->dy,
					    rect->width, rect->height);
			ae350_gfx_write_desc(fb, AE350_GFX_FILL, 0, dst, 0,
					     fb->stride, rect->width, rect->height,
					     color, 0, 0);
			rc = ae350_gfx2d_run(fb->gfx);
			if (!rc)
				ae350_fb_inval_rect(fb, rect->dx, rect->dy,
						    rect->width, rect->height);
		}
		spin_unlock_irqrestore(&fb->gfx_lock, flags);
		if (!rc)
			return;
	}
	sys_fillrect(info, rect);
	ae350_fb_clean_rect(fb, rect->dx, rect->dy, rect->width, rect->height);
}

static void ae350_fb_copyarea(struct fb_info *info,
			      const struct fb_copyarea *area)
{
	struct ae350_fb *fb = info->par;
	u32 src, dst;

	if (fb->gfx && area->width && area->height &&
	    (area->sy >= fb->height) == (area->dy >= fb->height)) {
		/*
		 * When copying rightward within overlapping columns on the same
		 * scanline, hardware forward beat traversal would overwrite
		 * source beats before they are read. Fall back to software copy.
		 */
		if (area->sy == area->dy && area->sx < area->dx &&
		    area->sx + area->width > area->dx) {
			sys_copyarea(info, area);
			ae350_fb_clean_rect(fb, area->dx, area->dy,
					    area->width, area->height);
			return;
		}
		src = ae350_phys(fb, area->sx, area->sy);
		dst = ae350_phys(fb, area->dx, area->dy);
		if ((src % 32) == (dst % 32)) {
			unsigned long flags;
			int rc = -EBUSY;

			spin_lock_irqsave(&fb->gfx_lock, flags);
			if (ae350_gfx_ready(fb)) {
				ae350_fb_clean_rect(fb, area->sx, area->sy,
						    area->width, area->height);
				ae350_fb_clean_rect(fb, area->dx, area->dy,
						    area->width, area->height);
				ae350_gfx_write_desc(fb, AE350_GFX_COPY, src, dst,
						     fb->stride, fb->stride, area->width,
						     area->height, 0, 0, 0);
				rc = ae350_gfx2d_run(fb->gfx);
				if (!rc)
					ae350_fb_inval_rect(fb, area->dx, area->dy,
							    area->width, area->height);
			}
			spin_unlock_irqrestore(&fb->gfx_lock, flags);
			if (!rc)
				return;
		}
	}
	sys_copyarea(info, area);
	ae350_fb_clean_rect(fb, area->dx, area->dy, area->width, area->height);
}

static void ae350_fb_imageblit(struct fb_info *info,
			       const struct fb_image *image)
{
	struct ae350_fb *fb = info->par;
	u32 dst;
	u32 bytes_per_row;
	size_t total;
	u32 fg, bg;

	if (fb->gfx && image->depth == 1 &&
	    image->width >= 1 && image->height >= 1) {
		dst = ae350_phys(fb, image->dx, image->dy);
		bytes_per_row = (image->width + 7) / 8;
		total = (size_t)bytes_per_row * image->height;
		if (total <= fb->scratch_size && (dst % fb->bpp) == 0 &&
		    fb->format != AE350_FMT_RGB888 &&
		    image->dy + image->height <=
			(image->dy >= fb->height ? fb->height * 2 : fb->height)) {
			unsigned long flags;
			int rc = -EBUSY;

			fg = image->fg_color < 16 ? ((u32 *)info->pseudo_palette)[image->fg_color] :
						    image->fg_color;
			bg = image->bg_color < 16 ? ((u32 *)info->pseudo_palette)[image->bg_color] :
						    image->bg_color;
			spin_lock_irqsave(&fb->gfx_lock, flags);
			if (ae350_gfx_ready(fb)) {
				memcpy(fb->scratch, image->data, total);
				ae350_cache_clean_range((u32)(uintptr_t)fb->scratch, total);
				ae350_fb_clean_rect(fb, image->dx, image->dy,
						    image->width, image->height);
				ae350_gfx_write_desc(fb, AE350_GFX_BLIT,
						     (u32)fb->scratch_dma, dst,
						     bytes_per_row, fb->stride,
						     image->width, image->height,
						     fg, bg,
						     bytes_per_row);
				rc = ae350_gfx2d_run(fb->gfx);
				if (!rc)
					ae350_fb_inval_rect(fb, image->dx, image->dy,
							    image->width, image->height);
			}
			spin_unlock_irqrestore(&fb->gfx_lock, flags);
			if (!rc)
				return;
		}
	}
	sys_imageblit(info, image);
	ae350_fb_clean_rect(fb, image->dx, image->dy, image->width, image->height);
}

static int ae350_fb_sync(struct fb_info *info)
{
	struct ae350_fb *fb = info->par;

	/* VA-type cache command needs the CPU virtual address. Note the
	 * framebuffer region is vmalloc-free dma-coherent memory; the
	 * CPU pointer is the cacheable kernel mapping. */
	ae350_cache_clean_range((u32)(uintptr_t)fb->cpu[0],
				fb->frame_bytes * 2);
	return 0;
}

static ssize_t ae350_fb_write(struct fb_info *info, const char __user *buf,
			      size_t count, loff_t *ppos)
{
	struct ae350_fb *fb = info->par;
	loff_t pos = *ppos;
	ssize_t ret;

	ret = fb_sys_write(info, buf, count, ppos);
	if (ret > 0 && pos < fb->frame_bytes * 2) {
		size_t len = min_t(size_t, ret, fb->frame_bytes * 2 - pos);
		ae350_cache_clean_range((u32)(uintptr_t)fb->cpu[0] + (u32)pos, len);
	}
	return ret;
}

struct ae350_fb_blend_req {
	__u32 src_x;
	__u32 src_y;
	__u32 dst_x;
	__u32 dst_y;
	__u32 width;
	__u32 height;
	__u8 alpha;
	__u8 pad[3];
	__u32 flags;
};

struct ae350_fb_scale_req {
	__u32 src_x;
	__u32 src_y;
	__u32 src_width;
	__u32 src_height;
	__u32 dst_x;
	__u32 dst_y;
	__u32 dst_width;
	__u32 dst_height;
	__u32 u0;
	__u32 v0;
	__u32 du_dx;
	__u32 dv_dy;
	__u8 alpha;
	__u8 pad[3];
	__u32 flags;
};

#define AE350_FBIO_BLEND _IOW('A', 0x01, struct ae350_fb_blend_req)
#define AE350_FBIO_SCALE _IOW('A', 0x02, struct ae350_fb_scale_req)

static bool ae350_fb_rect_in_bounds(const struct ae350_fb *fb,
				    u32 x, u32 y, u32 w, u32 h)
{
	u32 max_y = fb->height * 2u;

	return w && h && x < fb->width && y < max_y &&
	       w <= fb->width - x && h <= max_y - y;
}

static int ae350_fb_ioctl(struct fb_info *info, unsigned int cmd,
			  unsigned long arg)
{
	struct ae350_fb *fb = info->par;
	void __user *argp = (void __user *)arg;
	unsigned long flags;

	if (!fb->gfx || fb->format == AE350_FMT_RGB888 ||
	    fb->format == AE350_FMT_INDEX8)
		return -ENODEV;

	switch (cmd) {
	case AE350_FBIO_BLEND: {
		struct ae350_fb_blend_req req;
		u32 src_phys, dst_phys;
		int rc;

		if (copy_from_user(&req, argp, sizeof(req)))
			return -EFAULT;
		if (!ae350_fb_rect_in_bounds(fb, req.src_x, req.src_y,
					     req.width, req.height) ||
		    !ae350_fb_rect_in_bounds(fb, req.dst_x, req.dst_y,
					     req.width, req.height))
			return -EINVAL;
		src_phys = ae350_phys(fb, req.src_x, req.src_y);
		dst_phys = ae350_phys(fb, req.dst_x, req.dst_y);
		spin_lock_irqsave(&fb->gfx_lock, flags);
		if (!ae350_gfx_ready(fb)) {
			spin_unlock_irqrestore(&fb->gfx_lock, flags);
			return -EBUSY;
		}
		ae350_fb_clean_rect(fb, req.src_x, req.src_y, req.width, req.height);
		ae350_fb_clean_rect(fb, req.dst_x, req.dst_y, req.width, req.height);
		rc = ae350_gfx2d_blend(fb->gfx, src_phys, fb->stride, fb->format,
				       dst_phys, fb->stride, fb->format,
				       req.width, req.height, req.alpha, req.flags);
		if (!rc)
			ae350_fb_inval_rect(fb, req.dst_x, req.dst_y, req.width, req.height);
		spin_unlock_irqrestore(&fb->gfx_lock, flags);
		if (rc)
			return rc < 0 ? rc : -EIO;
		return 0;
	}
	case AE350_FBIO_SCALE: {
		struct ae350_fb_scale_req req;
		u32 src_phys, dst_phys, du_dx, dv_dy;
		int rc;

		if (copy_from_user(&req, argp, sizeof(req)))
			return -EFAULT;
		if (!ae350_fb_rect_in_bounds(fb, req.src_x, req.src_y,
					     req.src_width, req.src_height) ||
		    !ae350_fb_rect_in_bounds(fb, req.dst_x, req.dst_y,
					     req.dst_width, req.dst_height))
			return -EINVAL;
		src_phys = ae350_phys(fb, req.src_x, req.src_y);
		dst_phys = ae350_phys(fb, req.dst_x, req.dst_y);
		du_dx = req.du_dx ? req.du_dx : ((req.src_width << 16) / req.dst_width);
		dv_dy = req.dv_dy ? req.dv_dy : ((req.src_height << 16) / req.dst_height);
		spin_lock_irqsave(&fb->gfx_lock, flags);
		if (!ae350_gfx_ready(fb)) {
			spin_unlock_irqrestore(&fb->gfx_lock, flags);
			return -EBUSY;
		}
		ae350_fb_clean_rect(fb, req.src_x, req.src_y, req.src_width, req.src_height);
		ae350_fb_clean_rect(fb, req.dst_x, req.dst_y, req.dst_width, req.dst_height);
		rc = ae350_gfx2d_scale(fb->gfx, src_phys, fb->stride,
				       req.src_width, req.src_height, fb->format,
				       dst_phys, fb->stride,
				       req.dst_width, req.dst_height, fb->format,
				       req.u0, req.v0, du_dx, dv_dy,
				       req.alpha, req.flags);
		if (!rc)
			ae350_fb_inval_rect(fb, req.dst_x, req.dst_y, req.dst_width, req.dst_height);
		spin_unlock_irqrestore(&fb->gfx_lock, flags);
		if (rc)
			return rc < 0 ? rc : -EIO;
		return 0;
	}
	default:
		return -ENOTTY;
	}
}

static const struct fb_ops ae350_fb_ops = {
	.owner = THIS_MODULE,
	.fb_check_var = ae350_fb_check_var,
	.fb_set_par = ae350_fb_set_par,
	.fb_setcolreg = ae350_fb_setcolreg,
	.fb_blank = ae350_fb_blank,
	.fb_mmap = ae350_fb_mmap,
	.fb_pan_display = ae350_fb_pan_display,
	.fb_fillrect = ae350_fb_fillrect,
	.fb_copyarea = ae350_fb_copyarea,
	.fb_imageblit = ae350_fb_imageblit,
	.fb_ioctl = ae350_fb_ioctl,
	.fb_sync = ae350_fb_sync,
	.fb_read = fb_sys_read,
	.fb_write = ae350_fb_write,
};

static void ae350_gfx3d_acquire(struct ae350_fb *fb)
{
	unsigned long flags;

	mutex_lock(&fb->gfx3d_lock);
	spin_lock_irqsave(&fb->gfx_lock, flags);
	atomic_set(&fb->gfx3d_active, 1);
	spin_unlock_irqrestore(&fb->gfx_lock, flags);
}

static int ae350_gfx3d_release(struct ae350_fb *fb)
{
	unsigned long flags;
	int rc;

	rc = ae350_gfx3d_wait(fb->gfx, 0x200000u);
	spin_lock_irqsave(&fb->gfx_lock, flags);
	atomic_set(&fb->gfx3d_active, 0);
	spin_unlock_irqrestore(&fb->gfx_lock, flags);
	mutex_unlock(&fb->gfx3d_lock);
	return rc;
}

static long ae350_gfx3d_ioctl(struct file *filp, unsigned int cmd,
			      unsigned long arg)
{
	struct ae350_fb *fb = container_of(filp->private_data, struct ae350_fb,
					   gfx3d_misc);
	void __user *argp = (void __user *)arg;

	if (!fb->gfx || !fb->gfx3d_color_cpu || !fb->gfx3d_z_cpu)
		return -ENODEV;

	switch (cmd) {
	case AE350_GFX3D_IOC_INFO: {
		struct ae350_gfx3d_info info = {
			.version = AE350_GFX3D_ABI_VERSION,
			.max_width = AE350_GFX3D_MAX_W,
			.max_height = AE350_GFX3D_MAX_H,
			.max_batch = AE350_GFX3D_MAX_BATCH,
			.tmem_texels = AE350_GFX3D_TMEM_MAX_TEXELS,
			.fb_width = fb->width,
			.fb_height = fb->height,
			.fb_format = fb->format,
		};

		if (copy_to_user(argp, &info, sizeof(info)))
			return -EFAULT;
		return 0;
	}
	case AE350_GFX3D_IOC_CLEAR: {
		struct ae350_gfx3d_clear req;
		u32 stride_px;
		int rc = 0, rel_rc;

		if (copy_from_user(&req, argp, sizeof(req)))
			return -EFAULT;
		if (!req.width || !req.height ||
		    req.width > AE350_GFX3D_MAX_W ||
		    req.height > AE350_GFX3D_MAX_H)
			return -EINVAL;
		stride_px = ALIGN(req.width, 16u);
		ae350_gfx3d_acquire(fb);
		rc = ae350_gfx3d_wait(fb->gfx, 0x200000u);
		if (!rc && req.clear_color) {
			u32 *dst = (u32 *)fb->gfx3d_color_cpu;
			u32 y, x;
			for (y = 0; y < req.height; y++) {
				u32 *row = dst + y * stride_px;
				for (x = 0; x < req.width; x++)
					row[x] = req.color_xrgb8888;
			}
			ae350_cache_clean_range((u32)(uintptr_t)fb->gfx3d_color_cpu,
						stride_px * req.height * 4u);
		}
		if (!rc && req.clear_z) {
			u16 *dst = (u16 *)fb->gfx3d_z_cpu;
			u16 zv = (u16)(req.z_val & 0xFFFFu);
			u32 y, x;
			for (y = 0; y < req.height; y++) {
				u16 *row = dst + y * stride_px;
				for (x = 0; x < req.width; x++)
					row[x] = zv;
			}
			ae350_cache_clean_range((u32)(uintptr_t)fb->gfx3d_z_cpu,
						stride_px * req.height * 2u);
		}
		fb->gfx3d_stride_px = stride_px;
		rel_rc = ae350_gfx3d_release(fb);
		return rc ? rc : rel_rc;
	}
	case AE350_GFX3D_IOC_UPLOAD_TEX: {
		struct ae350_gfx3d_tex req;
		u16 *buf = NULL;
		u32 count = 0;
		u32 lw, lh, i;
		int rc, rel_rc;

		if (copy_from_user(&req, argp, sizeof(req)))
			return -EFAULT;
		if (req.width == 0 || req.height == 0 || req.rgb565_ptr == 0) {
			ae350_gfx3d_acquire(fb);
			rc = ae350_gfx3d_wait(fb->gfx, 0x200000u);
			if (!rc)
				writel(0u, fb->gfx + AE350_G3D_TEX_CFG);
			rel_rc = ae350_gfx3d_release(fb);
			return rc ? rc : rel_rc;
		}
		if ((req.rgb565_ptr >> 32) != 0)
			return -EFAULT;
		if (!is_power_of_2(req.width) || !is_power_of_2(req.height) ||
		    req.width > AE350_GFX3D_TMEM_MAX_TEXELS ||
		    req.height > AE350_GFX3D_TMEM_MAX_TEXELS)
			return -EINVAL;
		count = req.width * req.height;
		if (!count || count > AE350_GFX3D_TMEM_MAX_TEXELS)
			return -EINVAL;
		buf = kmalloc_array(count, sizeof(u16), GFP_KERNEL);
		if (!buf)
			return -ENOMEM;
		if (copy_from_user(buf, u64_to_user_ptr(req.rgb565_ptr),
				   count * sizeof(u16))) {
			kfree(buf);
			return -EFAULT;
		}
		lw = ilog2(req.width);
		lh = ilog2(req.height);
		ae350_gfx3d_acquire(fb);
		rc = ae350_gfx3d_wait(fb->gfx, 0x200000u);
		if (!rc) {
			u32 cfg = AE350_G3D_TEX_EN |
				  (req.bilinear ? AE350_G3D_BILINEAR_EN : 0u) |
				  ((req.wrap_u & 3u) << 2) |
				  ((req.wrap_v & 3u) << 4) |
				  ((lw & 0xFu) << 7) |
				  ((lh & 0xFu) << 11);
			writel(cfg, fb->gfx + AE350_G3D_TEX_CFG);
			writel(0u, fb->gfx + AE350_G3D_TMEM_ADDR);
			for (i = 0; i < count; i++) {
				u16 p = buf[i];
				u32 r5 = (p >> 11) & 0x1Fu;
				u32 g6 = (p >> 5) & 0x3Fu;
				u32 b5 = p & 0x1Fu;
				u32 r8 = (r5 << 3) | (r5 >> 2);
				u32 g8 = (g6 << 2) | (g6 >> 4);
				u32 b8 = (b5 << 3) | (b5 >> 2);
				writel(0xFF000000u | (r8 << 16) | (g8 << 8) | b8,
				       fb->gfx + AE350_G3D_TMEM_DATA);
			}
		}
		rel_rc = ae350_gfx3d_release(fb);
		kfree(buf);
		return rc ? rc : rel_rc;
	}
	case AE350_GFX3D_IOC_SUBMIT: {
		struct ae350_gfx3d_submit req;
		struct ae350_gfx3d_tri *chunk;
		const struct ae350_gfx3d_tri __user *user_tris;
		u32 stride_px, done = 0, submitted = 0;
		u32 last_z_cfg = 0xFFFFFFFFu, last_tex_cfg = 0xFFFFFFFFu;
		int rc = 0, rel_rc;

		if (copy_from_user(&req, argp, sizeof(req)))
			return -EFAULT;
		if (!req.width || !req.height ||
		    req.width > AE350_GFX3D_MAX_W ||
		    req.height > AE350_GFX3D_MAX_H ||
		    req.count > AE350_GFX3D_MAX_BATCH)
			return -EINVAL;
		if (req.count == 0) {
			req.pixels_out = readl(fb->gfx + AE350_G3D_PIXELS_OUT);
			if (copy_to_user(argp, &req, sizeof(req)))
				return -EFAULT;
			return 0;
		}
		if (!req.tris_ptr || (req.tris_ptr >> 32) != 0)
			return -EFAULT;
		user_tris = u64_to_user_ptr(req.tris_ptr);
		chunk = kmalloc_array(64u, sizeof(*chunk), GFP_KERNEL);
		if (!chunk)
			return -ENOMEM;

		stride_px = ALIGN(req.width, 16u);
		ae350_gfx3d_acquire(fb);
		rc = ae350_gfx3d_wait(fb->gfx, 0x200000u);
		if (rc)
			goto out_submit_release;

		ae350_gfx3d_init_shader(fb->gfx);
		writel((u32)fb->gfx3d_color_dma, fb->gfx + AE350_G3D_FB_ADDR);
		writel(stride_px, fb->gfx + AE350_G3D_FB_STRIDE);
		writel((u32)fb->gfx3d_z_dma, fb->gfx + AE350_G3D_ZBUF_ADDR);
		fb->gfx3d_stride_px = stride_px;

		while (done < req.count) {
			u32 n = min_t(u32, req.count - done, 64u);
			u32 i, j;

			if (copy_from_user(chunk, user_tris + done,
					   n * sizeof(*chunk))) {
				rc = -EFAULT;
				goto out_submit_release;
			}
			for (i = 0; i < n; i++) {
				rc = ae350_gfx3d_wait(fb->gfx, 0x200000u);
				if (rc)
					goto out_submit_release;
				if (chunk[i].z_cfg != last_z_cfg) {
					writel(chunk[i].z_cfg,
					       fb->gfx + AE350_G3D_Z_CFG);
					last_z_cfg = chunk[i].z_cfg;
				}
				if (chunk[i].tex_cfg != last_tex_cfg) {
					writel(chunk[i].tex_cfg,
					       fb->gfx + AE350_G3D_TEX_CFG);
					last_tex_cfg = chunk[i].tex_cfg;
				}
				for (j = 0; j < 24u; j++)
					writel(chunk[i].words[j],
					       fb->gfx + AE350_G3D_TRI_BASE + 4u * j);
				writel(1u, fb->gfx + AE350_G3D_CTRL);
				submitted++;
				if ((submitted & 31u) == 0u)
					cond_resched();
			}
			done += n;
		}
		rc = ae350_gfx3d_wait(fb->gfx, 0x200000u);
		if (!rc)
			req.pixels_out = readl(fb->gfx + AE350_G3D_PIXELS_OUT);

out_submit_release:
		rel_rc = ae350_gfx3d_release(fb);
		kfree(chunk);
		if (rc)
			return rc;
		if (rel_rc)
			return rel_rc;
		if (copy_to_user(argp, &req, sizeof(req)))
			return -EFAULT;
		return 0;
	}
	case AE350_GFX3D_IOC_READBACK: {
		struct ae350_gfx3d_readback req;
		u8 __user *dst_user;
		u16 *row565 = NULL;
		u32 stride_px, y, x;
		int rc;

		if (copy_from_user(&req, argp, sizeof(req)))
			return -EFAULT;
		if (!req.width || !req.height ||
		    req.width > AE350_GFX3D_MAX_W ||
		    req.height > AE350_GFX3D_MAX_H)
			return -EINVAL;
		if (req.fmt != AE350_GFX3D_READBACK_RGB565 &&
		    req.fmt != AE350_GFX3D_READBACK_XRGB8888)
			return -EINVAL;
		if (req.dst_stride < req.width * (req.fmt == AE350_GFX3D_READBACK_RGB565 ? 2u : 4u))
			return -EINVAL;
		if (!req.dst_ptr || (req.dst_ptr >> 32) != 0)
			return -EFAULT;
		dst_user = u64_to_user_ptr(req.dst_ptr);
		if (req.fmt == AE350_GFX3D_READBACK_RGB565) {
			row565 = kmalloc_array(req.width, sizeof(u16), GFP_KERNEL);
			if (!row565)
				return -ENOMEM;
		}

		stride_px = ALIGN(req.width, 16u);
		mutex_lock(&fb->gfx3d_lock);
		rc = ae350_gfx3d_wait(fb->gfx, 0x200000u);
		if (!rc) {
			const u32 *src = (const u32 *)fb->gfx3d_color_cpu;

			ae350_cache_inval_range((u32)(uintptr_t)fb->gfx3d_color_cpu,
						stride_px * req.height * 4u);
			for (y = 0; y < req.height; y++) {
				const u32 *src_row = src + y * stride_px;
				u8 __user *dst_row = dst_user + (size_t)y * req.dst_stride;

				if (req.fmt == AE350_GFX3D_READBACK_XRGB8888) {
					if (copy_to_user(dst_row, src_row, req.width * 4u)) {
						rc = -EFAULT;
						break;
					}
				} else {
					for (x = 0; x < req.width; x++) {
						u32 p = src_row[x];
						row565[x] = (u16)(((p >> 8) & 0xF800u) |
								  ((p >> 5) & 0x07E0u) |
								  ((p >> 3) & 0x001Fu));
					}
					if (copy_to_user(dst_row, row565, req.width * 2u)) {
						rc = -EFAULT;
						break;
					}
				}
			}
		}
		mutex_unlock(&fb->gfx3d_lock);
		kfree(row565);
		return rc;
	}
	default:
		return -ENOTTY;
	}
}

static const struct file_operations ae350_gfx3d_fops = {
	.owner = THIS_MODULE,
	.unlocked_ioctl = ae350_gfx3d_ioctl,
	.llseek = noop_llseek,
};

static ssize_t dma_status_show(struct device *dev,
			       struct device_attribute *attr, char *buf)
{
	struct fb_info *info = dev_get_drvdata(dev);
	struct ae350_fb *fb = info->par;

	return sysfs_emit(buf,
		"frames=%u reads=%u underflows=%u missing=%u errors=%u hpd=%08x mode=%u format=%u gfx=%s enable=%u completed=%u address_error=%u state=%u\n",
		readl(fb->csr + AE350_V_FRAMES), readl(fb->csr + AE350_V_READS),
		readl(fb->csr + AE350_V_UNDERFLOWS),
		readl(fb->csr + AE350_V_MISSING),
		readl(fb->csr + AE350_V_ERRORS), readl(fb->csr + AE350_V_HPD),
		readl(fb->csr + AE350_V_MODE), readl(fb->csr + AE350_V_FORMAT),
		fb->gfx ? "yes" : "no",
		readl(fb->csr + AE350_V_ENABLE),
		readl(fb->csr + AE350_V_FRAMES),
		readl(fb->csr + AE350_V_ADDRESS_ERROR),
		readl(fb->csr + AE350_V_STATUS) &
		(AE350_V_STATUS_STOPPED | 1u));
}
static DEVICE_ATTR_RO(dma_status);

static int ae350_fb_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct resource *res;
	struct fb_info *info;
	struct ae350_fb *fb;
	struct ae350_mode_info mode;
	struct ae350_pll_result pll;
	const char *format_str = "rgb565";
	u32 custom[11];
	u32 pixel_hz;
	int ret;
	bool use_custom = false;

	if (!dev_is_dma_coherent(dev))
		return dev_err_probe(dev, -EINVAL, "video requires coherent DMA\n");
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
	if (IS_ERR(fb->csr)) {
		ret = PTR_ERR(fb->csr);
		goto release;
	}
	if (readl(fb->csr) != AE350_VIDEO_ID) {
		ret = -ENODEV;
		goto release;
	}
	spin_lock_init(&fb->gfx_lock);
	static const u8 bpp_table[] = { 2, 3, 4, 2, 1 };
	fb->format = AE350_FMT_RGB565;
	if (!of_property_read_string(dev->of_node, "riscv-mini,format",
				     &format_str)) {
		if (!strcmp(format_str, "xrgb1555"))
			fb->format = AE350_FMT_XRGB1555;
		else if (!strcmp(format_str, "xrgb8888"))
			fb->format = AE350_FMT_XRGB8888;
		else if (!strcmp(format_str, "rgb888"))
			fb->format = AE350_FMT_RGB888;
		else if (!strcmp(format_str, "index8"))
			fb->format = AE350_FMT_INDEX8;
		else if (strcmp(format_str, "rgb565")) {
			ret = -EINVAL;
			goto release;
		}
	}
	fb->bpp = (fb->format < ARRAY_SIZE(bpp_table)) ? bpp_table[fb->format] : 2;
	fb->mode_index = 0;
	of_property_read_u32(dev->of_node, "riscv-mini,mode", &fb->mode_index);
	if (of_property_read_u32_array(dev->of_node, "riscv-mini,custom-mode",
				       custom, ARRAY_SIZE(custom)) == 0)
		use_custom = true;
	if (use_custom) {
		mode.width = custom[0];
		mode.height = custom[1];
		mode.h_front = custom[2];
		mode.h_sync = custom[3];
		mode.h_back = custom[4];
		mode.v_front = custom[5];
		mode.v_sync = custom[6];
		mode.v_back = custom[7];
		mode.refresh_millihz = custom[8];
		mode.h_positive = custom[9];
		mode.v_positive = custom[10];
		{
			u32 bpp = fb->bpp;
			if (((mode.width * bpp) & 31u) != 0) {
				ret = -EINVAL;
				goto release;
			}
		}
		pixel_hz = (u32)div_u64((u64)(mode.width + mode.h_front +
					       mode.h_sync + mode.h_back) *
				     (mode.height + mode.v_front +
				      mode.v_sync + mode.v_back) *
				     mode.refresh_millihz, 1000);
		if (!pixel_hz || pixel_hz > 74250000u ||
		    ae350_video_pll_search(pixel_hz, &pll)) {
			ret = -EINVAL;
			goto release;
		}
	} else {
		if (fb->mode_index >= readl(fb->csr + AE350_V_MODE_COUNT)) {
			ret = -EINVAL;
			goto release;
		}
	}
	/* Stop any running scanout before reconfiguring. */
	ae350_video_stop(fb->csr);
	if (use_custom)
		ret = ae350_video_set_custom_mode(fb->csr, &mode, fb->format,
						  &pll, pixel_hz);
	else
		ret = ae350_video_set_mode(fb->csr, fb->mode_index, fb->format);
	if (ret) {
		ret = -EIO;
		goto release;
	}
	if (use_custom) {
		fb->width = mode.width;
		fb->height = mode.height;
	} else {
		ae350_read_mode_info(fb->csr, fb->mode_index, &mode);
		fb->width = mode.width;
		fb->height = mode.height;
	}
	fb->stride = fb->width * fb->bpp;
	fb->frame_bytes = fb->stride * fb->height;
	/* One allocation for both buffers: mmap exposes them contiguously. */
	fb->cpu[0] = dma_alloc_coherent(dev, fb->frame_bytes * 2, &fb->dma[0],
					GFP_KERNEL);
	if (!fb->cpu[0]) {
		ret = -ENOMEM;
		goto release;
	}
	fb->dma[1] = fb->dma[0] + fb->frame_bytes;
	fb->cpu[1] = (u8 *)fb->cpu[0] + fb->frame_bytes;
	if (fb->dma[0] < 4096 || (fb->dma[0] & 31) ||
	    fb->dma[0] > 0x40000000u - fb->frame_bytes * 2) {
		ret = -EINVAL;
		goto free1;
	}
	memset(fb->cpu[0], 0, fb->frame_bytes * 2);
	fb->scratch_size = 4096;
	fb->scratch = dma_alloc_coherent(dev, fb->scratch_size, &fb->scratch_dma,
					 GFP_KERNEL);
	if (!fb->scratch) {
		ret = -ENOMEM;
		goto free1;
	}
	writel(fb->dma[0], fb->csr + AE350_V_BASE0);
	writel(fb->dma[1], fb->csr + AE350_V_BASE1);
	writel(fb->stride, fb->csr + AE350_V_STRIDE);
	ret = ae350_video_enable(fb->csr, 1);
	if (ret) {
		ret = -EIO;
		goto free1;
	}
	ret = ae350_present(fb, 0);
	if (ret) {
		ret = -EIO;
		goto free1;
	}
	fb->active_slot = 0;
	mutex_init(&fb->gfx3d_lock);
	atomic_set(&fb->gfx3d_active, 0);
	/* Optional second register range: GFX2D accelerator (+GFX3D engine). */
	res = platform_get_resource(pdev, IORESOURCE_MEM, 1);
	if (res) {
		fb->gfx = devm_ioremap_resource(dev, res);
		if (IS_ERR(fb->gfx))
			fb->gfx = NULL;
		else if (readl(fb->gfx) != AE350_GFX2D_ID)
			fb->gfx = NULL;
	}
	if (fb->gfx) {
		fb->gfx3d_color_bytes = PAGE_ALIGN(AE350_GFX3D_MAX_W * AE350_GFX3D_MAX_H * 4u);
		fb->gfx3d_z_bytes = PAGE_ALIGN(AE350_GFX3D_MAX_W * AE350_GFX3D_MAX_H * 2u);
		fb->gfx3d_color_cpu = dma_alloc_coherent(dev, fb->gfx3d_color_bytes,
							 &fb->gfx3d_color_dma, GFP_KERNEL);
		fb->gfx3d_z_cpu = dma_alloc_coherent(dev, fb->gfx3d_z_bytes,
						     &fb->gfx3d_z_dma, GFP_KERNEL);
		if (!fb->gfx3d_color_cpu || !fb->gfx3d_z_cpu ||
		    fb->gfx3d_color_dma < 4096 || (fb->gfx3d_color_dma & 31u) ||
		    fb->gfx3d_color_dma > 0x40000000u - fb->gfx3d_color_bytes ||
		    fb->gfx3d_z_dma < 4096 || (fb->gfx3d_z_dma & 31u) ||
		    fb->gfx3d_z_dma > 0x40000000u - fb->gfx3d_z_bytes) {
			ret = (!fb->gfx3d_color_cpu || !fb->gfx3d_z_cpu) ? -ENOMEM : -EINVAL;
			goto free1;
		}
		memset(fb->gfx3d_color_cpu, 0, fb->gfx3d_color_bytes);
		memset(fb->gfx3d_z_cpu, 0xff, fb->gfx3d_z_bytes);
		ae350_cache_clean_range((u32)(uintptr_t)fb->gfx3d_color_cpu,
					fb->gfx3d_color_bytes);
		ae350_cache_clean_range((u32)(uintptr_t)fb->gfx3d_z_cpu,
					fb->gfx3d_z_bytes);
		fb->gfx3d_misc.minor = MISC_DYNAMIC_MINOR;
		fb->gfx3d_misc.name = "ae350-gfx3d";
		fb->gfx3d_misc.fops = &ae350_gfx3d_fops;
		fb->gfx3d_misc.parent = dev;
		fb->gfx3d_misc.mode = 0666;
		ret = misc_register(&fb->gfx3d_misc);
		if (ret)
			goto free1;
		fb->gfx3d_registered = true;
	}
	info->screen_buffer = fb->cpu[0];
	info->screen_size = fb->frame_bytes * 2;
	info->flags = FBINFO_VIRTFB;
	info->fbops = &ae350_fb_ops;
	info->pseudo_palette = fb->palette;
	strscpy(info->fix.id, "ae350-video", sizeof(info->fix.id));
	info->fix.smem_start = fb->dma[0];
	info->fix.smem_len = info->screen_size;
	info->fix.type = FB_TYPE_PACKED_PIXELS;
	info->fix.visual = (fb->format == AE350_FMT_INDEX8) ?
			   FB_VISUAL_PSEUDOCOLOR : FB_VISUAL_TRUECOLOR;
	info->fix.line_length = fb->stride;
	info->fix.ypanstep = 1;
	info->fix.ywrapstep = 0;
	info->var.xres = info->var.xres_virtual = fb->width;
	info->var.yres = fb->height;
	info->var.yres_virtual = fb->height * 2;
	info->var.bits_per_pixel = fb->bpp * 8;
	if (fb->format == AE350_FMT_INDEX8) {
		info->var.red = (struct fb_bitfield){0, 8, 0};
		info->var.green = (struct fb_bitfield){0, 8, 0};
		info->var.blue = (struct fb_bitfield){0, 8, 0};
		info->var.transp = (struct fb_bitfield){0, 0, 0};
	} else if (fb->format == AE350_FMT_XRGB1555) {
		info->var.red = (struct fb_bitfield){10, 5, 0};
		info->var.green = (struct fb_bitfield){5, 5, 0};
		info->var.blue = (struct fb_bitfield){0, 5, 0};
		info->var.transp = (struct fb_bitfield){15, 1, 0};
	} else if (fb->format == AE350_FMT_XRGB8888) {
		info->var.red = (struct fb_bitfield){16, 8, 0};
		info->var.green = (struct fb_bitfield){8, 8, 0};
		info->var.blue = (struct fb_bitfield){0, 8, 0};
		info->var.transp = (struct fb_bitfield){24, 8, 0};
	} else if (fb->format == AE350_FMT_RGB888) {
		info->var.red = (struct fb_bitfield){16, 8, 0};
		info->var.green = (struct fb_bitfield){8, 8, 0};
		info->var.blue = (struct fb_bitfield){0, 8, 0};
	} else {
		info->var.red = (struct fb_bitfield){11, 5, 0};
		info->var.green = (struct fb_bitfield){5, 6, 0};
		info->var.blue = (struct fb_bitfield){0, 5, 0};
	}
	info->var.width = info->var.height = -1;
	platform_set_drvdata(pdev, info);
	ret = register_framebuffer(info);
	if (ret)
		goto free1;
	ret = device_create_file(dev, &dev_attr_dma_status);
	if (ret) {
		unregister_framebuffer(info);
		goto free1;
	}
	dev_info(dev, "fb%d: %ux%u %s double-buffered at %08x%s%s\n", info->node,
		 fb->width, fb->height,
		 fb->format == AE350_FMT_INDEX8 ? "INDEX8" :
		 (fb->format == AE350_FMT_XRGB8888 ? "XRGB8888" :
		  (fb->format == AE350_FMT_RGB888 ? "RGB888" :
		   (fb->format == AE350_FMT_XRGB1555 ? "XRGB1555" : "RGB565"))),
		 (u32)fb->dma[0], fb->gfx ? " (gfx2d accel)" : "",
		 fb->gfx3d_registered ? " (/dev/ae350-gfx3d)" : "");
	return 0;

free1:
	if (fb->gfx3d_registered)
		misc_deregister(&fb->gfx3d_misc);
	if (fb->gfx3d_z_cpu)
		dma_free_coherent(dev, fb->gfx3d_z_bytes, fb->gfx3d_z_cpu,
				  fb->gfx3d_z_dma);
	if (fb->gfx3d_color_cpu)
		dma_free_coherent(dev, fb->gfx3d_color_bytes,
				  fb->gfx3d_color_cpu, fb->gfx3d_color_dma);
	if (fb->scratch)
		dma_free_coherent(dev, fb->scratch_size, fb->scratch,
				  fb->scratch_dma);
	if (fb->cpu[0]) {
		if (ae350_video_enable(fb->csr, 0))
			dev_err(dev, "video stop timed out on probe error\n");
		else
			dma_free_coherent(dev, fb->frame_bytes * 2, fb->cpu[0],
					  fb->dma[0]);
	}
release:
	framebuffer_release(info);
	return ret;
}

static void ae350_fb_shutdown(struct platform_device *pdev)
{
	struct fb_info *info = platform_get_drvdata(pdev);
	struct ae350_fb *fb = info ? info->par : NULL;

	if (fb && ae350_video_enable(fb->csr, 0))
		dev_err(&pdev->dev, "video stop timed out at shutdown\n");
}

static const struct of_device_id ae350_fb_of_match[] = {
	{ .compatible = "riscv-mini,ae350-video" }, { }
};
MODULE_DEVICE_TABLE(of, ae350_fb_of_match);

static struct platform_driver ae350_fb_driver = {
	.probe = ae350_fb_probe,
	.shutdown = ae350_fb_shutdown,
	.driver = { .name = "ae350-video-fb", .of_match_table = ae350_fb_of_match,
		    .suppress_bind_attrs = true },
};
module_platform_driver(ae350_fb_driver);
MODULE_LICENSE("GPL");
