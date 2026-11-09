// SPDX-License-Identifier: GPL-2.0-only
/*
 * KMS/DRM driver for the AE350 video engine (Tang Mega 138K Pro).
 *
 * drm_simple_display_pipe based: the connector exposes the gateware's
 * fixed mode table (read from MODE_INFO registers) plus an optional DT
 * custom mode; modesets program either the fixed index or the CUS register
 * bank (geometry + PLL search, same limits as the BSP).  Framebuffers are
 * GEM DMA objects; update() copies the plane framebuffer into the scanout
 * buffer (fbdev emulation therefore works through the same path).
 */
#include <linux/dma-mapping.h>
#include <linux/dma-map-ops.h>
#include <linux/fb.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>

#include <drm/drm_aperture.h>
#include <drm/drm_atomic.h>
#include <drm/drm_atomic_helper.h>
#include <drm/drm_atomic_state_helper.h>
#include <drm/drm_connector.h>
#include <drm/drm_device.h>
#include <drm/drm_drv.h>
#include <drm/drm_fb_helper.h>
#include <drm/drm_fbdev_dma.h>
#include <drm/drm_format_helper.h>
#include <drm/drm_framebuffer.h>
#include <drm/drm_gem_dma_helper.h>
#include <drm/drm_gem_framebuffer_helper.h>
#include <drm/drm_managed.h>
#include <drm/drm_modeset_helper_vtables.h>
#include <drm/drm_plane.h>
#include <drm/drm_probe_helper.h>
#include <drm/drm_simple_kms_helper.h>

#include "riscv_mini_ae350_regs.h"

#define DRIVER_NAME "ae350-drm"
#define DRIVER_DESC "KMS driver for AE350 video engine"
#define DRIVER_DATE "20251107"
#define DRIVER_MAJOR 1
#define DRIVER_MINOR 0

static const u32 ae350_drm_formats[] = {
	DRM_FORMAT_XRGB8888,
	DRM_FORMAT_RGB888,
	DRM_FORMAT_RGB565,
	DRM_FORMAT_XRGB1555,
};

/* Gateware format ids match the MODE_CONFIG[15:8] field. */
static int ae350_drm_format_id(u32 fourcc)
{
	switch (fourcc) {
	case DRM_FORMAT_RGB565:
		return AE350_FMT_RGB565;
	case DRM_FORMAT_RGB888:
		return AE350_FMT_RGB888;
	case DRM_FORMAT_XRGB8888:
		return AE350_FMT_XRGB8888;
	case DRM_FORMAT_XRGB1555:
		return AE350_FMT_XRGB1555;
	default:
		return -1;
	}
}

static u32 ae350_drm_bpp(u32 format_id)
{
	switch (format_id) {
	case AE350_FMT_RGB565:
	case AE350_FMT_XRGB1555:
		return 2;
	case AE350_FMT_RGB888:
		return 3;
	case AE350_FMT_XRGB8888:
		return 4;
	default:
		return 0;
	}
}

struct ae350_drm {
	struct drm_device dev;
	struct drm_simple_display_pipe pipe;
	struct drm_connector connector;
	void __iomem *csr;
	void __iomem *gfx;
	u32 width;
	u32 height;
	u32 format_id;
	u32 fourcc;
	/* Scanout target buffer (copy destination of plane updates). */
	dma_addr_t dma;
	void *cpu;
	size_t size;
	u32 stride;
};

static void ae350_drm_copy_fb(struct ae350_drm *drm,
			      struct drm_plane_state *plane_state)
{
	struct drm_framebuffer *fb;
	struct iosys_map dst_map, *src_maps, *src_data;
	struct drm_format_conv_state fmtcnv_state = DRM_FORMAT_CONV_STATE_INIT;
	struct drm_rect clip;
	u32 src_x, src_y, src_w, src_h;
	int ret;

	if (!drm->cpu || !plane_state || !plane_state->fb)
		return;
	fb = plane_state->fb;

	src_maps = kcalloc(4, sizeof(*src_maps), GFP_KERNEL);
	src_data = kcalloc(4, sizeof(*src_data), GFP_KERNEL);
	if (!src_maps || !src_data) {
		kfree(src_maps);
		kfree(src_data);
		return;
	}
	iosys_map_set_vaddr(&dst_map, drm->cpu);
	ret = drm_gem_fb_vmap(fb, src_maps, src_data);
	if (ret) {
		drm_err(&drm->dev, "framebuffer vmap failed: %d\n", ret);
		goto free_maps;
	}
	src_x = plane_state->src_x >> 16;
	src_y = plane_state->src_y >> 16;
	src_w = plane_state->src_w >> 16 ? (plane_state->src_w >> 16) : drm->width;
	src_h = plane_state->src_h >> 16 ? (plane_state->src_h >> 16) : drm->height;
	if (src_w > drm->width)
		src_w = drm->width;
	if (src_h > drm->height)
		src_h = drm->height;

	if (drm->gfx && drm->fourcc == fb->format->format &&
	    drm->format_id != AE350_FMT_RGB888 &&
	    readl(drm->gfx) == AE350_GFX2D_ID &&
	    !(readl(drm->gfx + AE350_G_STATUS) & AE350_G_STATUS_BUSY)) {
		struct drm_gem_dma_object *gem = to_drm_gem_dma_obj(fb->obj[0]);

		if (gem && gem->dma_addr) {
			u32 bpp = ae350_drm_bpp(drm->format_id);
			u32 src_phys = (u32)gem->dma_addr + fb->offsets[0] +
				       src_y * fb->pitches[0] + src_x * bpp;
			u32 dst_phys = (u32)drm->dma;

			if ((src_phys % 32) == (dst_phys % 32) &&
			    (fb->pitches[0] % 32) == (drm->stride % 32)) {
				u32 r;

				for (r = 0; r < src_h; r++) {
					u32 s_va = (u32)(uintptr_t)src_data[0].vaddr +
						   (src_y + r) * fb->pitches[0] + src_x * bpp;
					u32 d_va = (u32)(uintptr_t)drm->cpu + r * drm->stride;
					ae350_cache_clean_range(s_va, src_w * bpp);
					ae350_cache_clean_range(d_va, src_w * bpp);
				}

				writel(AE350_GFX_COPY, drm->gfx + AE350_G_OP);
				writel(src_phys, drm->gfx + AE350_G_SRC_ADDR);
				writel(dst_phys, drm->gfx + AE350_G_DST_ADDR);
				writel(fb->pitches[0], drm->gfx + AE350_G_SRC_STRIDE);
				writel(drm->stride, drm->gfx + AE350_G_DST_STRIDE);
				writel(src_w, drm->gfx + AE350_G_WIDTH);
				writel(src_h, drm->gfx + AE350_G_HEIGHT);
				writel(0, drm->gfx + AE350_G_FG_COLOR);
				writel(0, drm->gfx + AE350_G_BG_COLOR);
				writel(0, drm->gfx + AE350_G_BLIT_SRC_WB);
				writel(drm->format_id, drm->gfx + AE350_G_FORMAT);
				if (!ae350_gfx2d_run(drm->gfx)) {
					for (r = 0; r < src_h; r++) {
						u32 d_va = (u32)(uintptr_t)drm->cpu + r * drm->stride;
						ae350_cache_inval_range(d_va, src_w * bpp);
					}
					drm_gem_fb_vunmap(fb, src_maps);
					goto free_maps;
				}
			}
		}
	}

	drm_rect_init(&clip, src_x, src_y, src_w, src_h);
	ret = drm_fb_blit(&dst_map, &drm->stride, drm->fourcc, src_data,
			  fb, &clip, &fmtcnv_state);
	if (ret)
		drm_err(&drm->dev, "framebuffer blit failed: %d\n", ret);
	drm_gem_fb_vunmap(fb, src_maps);
	/* VA-type cache command needs the CPU virtual address. */
	ae350_cache_clean_range((u32)(uintptr_t)drm->cpu, (u32)drm->size);
free_maps:
	drm_format_conv_state_release(&fmtcnv_state);
	kfree(src_data);
	kfree(src_maps);
}

static int ae350_drm_present(struct ae350_drm *drm, u32 slot)
{
	u32 status = readl(drm->csr + AE350_V_STATUS);
	int ret;

	if ((status & 1u) && ((status >> 1) & 1u) == slot)
		return 0;
	if (!(readl(drm->csr + 8) & 0x40u))
		return 0;
	if (!readl(drm->csr + AE350_V_ENABLE)) {
		ret = ae350_video_enable(drm->csr, 1);
		if (ret)
			return ret;
	}
	ret = ae350_video_commit(drm->csr, 1u | (slot << 1));
	if (ret)
		return ret;
	status = readl(drm->csr + AE350_V_STATUS);
	return ((status & 3u) == (1u | (slot << 1)) &&
		readl(drm->csr + AE350_V_ACTIVE_BASE) == drm->dma) ?
		0 : -EIO;
}

static bool ae350_drm_match_mode(const struct drm_display_mode *mode,
				 const struct ae350_mode_info *info)
{
	u32 h_front = mode->hsync_start - mode->hdisplay;
	u32 h_sync = mode->hsync_end - mode->hsync_start;
	u32 h_back = mode->htotal - mode->hsync_end;
	u32 v_front = mode->vsync_start - mode->vdisplay;
	u32 v_sync = mode->vsync_end - mode->vsync_start;
	u32 v_back = mode->vtotal - mode->vsync_end;
	bool h_pos = !!(mode->flags & DRM_MODE_FLAG_PHSYNC);
	bool v_pos = !!(mode->flags & DRM_MODE_FLAG_PVSYNC);
	u32 mode_clock_hz = (u32)mode->clock * 1000u;
	u32 clock_diff = mode_clock_hz > info->actual_pixel_hz ?
		(mode_clock_hz - info->actual_pixel_hz) :
		(info->actual_pixel_hz - mode_clock_hz);

	if (info->width != (u32)mode->hdisplay ||
	    info->height != (u32)mode->vdisplay ||
	    info->h_total != (u32)mode->htotal ||
	    info->v_total != (u32)mode->vtotal ||
	    info->h_front != h_front ||
	    info->h_sync != h_sync ||
	    info->h_back != h_back ||
	    info->v_front != v_front ||
	    info->v_sync != v_sync ||
	    info->v_back != v_back ||
	    info->h_positive != h_pos ||
	    info->v_positive != v_pos)
		return false;

	return clock_diff <= 1000u;
}

static void ae350_drm_pipe_enable(struct drm_simple_display_pipe *pipe,
				  struct drm_crtc_state *crtc_state,
				  struct drm_plane_state *plane_state)
{
	struct ae350_drm *drm = pipe->crtc.dev->dev_private;
	struct drm_device *dev = &drm->dev;
	struct drm_display_mode *mode = &crtc_state->mode;
	struct drm_framebuffer *fb = plane_state->fb;
	struct ae350_mode_info info;
	struct ae350_pll_result pll;
	size_t want;
	int format_id, ret;
	u32 i, count, pixel_hz;

	format_id = ae350_drm_format_id(fb->format->format);
	if (format_id < 0) {
		drm_err(dev, "unsupported format %p4cc\n", &fb->format->format);
		return;
	}
	if (drm->cpu && (drm->width != (u32)mode->hdisplay ||
			 drm->height != (u32)mode->vdisplay ||
			 drm->format_id != format_id)) {
		dma_free_coherent(dev->dev, drm->size, drm->cpu, drm->dma);
		drm->cpu = NULL;
		drm->size = 0;
	}
	if (!drm->cpu) {
		drm->stride = (u32)mode->hdisplay * ae350_drm_bpp(format_id);
		want = drm->stride * mode->vdisplay;
		drm->cpu = dma_alloc_coherent(dev->dev, want, &drm->dma,
					      GFP_KERNEL);
		if (!drm->cpu)
			return;
		drm->size = want;
		memset(drm->cpu, 0, want);
	}
	drm->width = mode->hdisplay;
	drm->height = mode->vdisplay;
	drm->format_id = format_id;
	drm->fourcc = fb->format->format;
	/* Find an exact fixed-mode match first (complete timing, sync, and clock). */
	count = readl(drm->csr + AE350_V_MODE_COUNT);
	for (i = 0; i < count; i++) {
		ae350_read_mode_info(drm->csr, i, &info);
		if (ae350_drm_match_mode(mode, &info))
			break;
	}
	ae350_video_stop(drm->csr);
	if (i < count) {
		ret = ae350_video_set_mode(drm->csr, i, format_id);
	} else {
		info.width = mode->hdisplay;
		info.height = mode->vdisplay;
		info.h_front = mode->hsync_start - mode->hdisplay;
		info.h_sync = mode->hsync_end - mode->hsync_start;
		info.h_back = mode->htotal - mode->hsync_end;
		info.v_front = mode->vsync_start - mode->vdisplay;
		info.v_sync = mode->vsync_end - mode->vsync_start;
		info.v_back = mode->vtotal - mode->vsync_end;
		info.h_positive = !!(mode->flags & DRM_MODE_FLAG_PHSYNC);
		info.v_positive = !!(mode->flags & DRM_MODE_FLAG_PVSYNC);
		pixel_hz = mode->clock * 1000u;
		ret = ae350_video_pll_search(pixel_hz, &pll);
		if (ret)
			return;
		ret = ae350_video_set_custom_mode(drm->csr, &info, format_id,
						  &pll, pixel_hz);
	}
	if (ret) {
		drm_err(dev, "mode set failed: %d\n", ret);
		return;
	}
	writel(drm->dma, drm->csr + AE350_V_BASE0);
	writel(drm->dma, drm->csr + AE350_V_BASE1);
	writel(drm->stride, drm->csr + AE350_V_STRIDE);
	ret = ae350_video_enable(drm->csr, 1);
	if (ret) {
		drm_err(dev, "scanout enable failed: %d\n", ret);
		return;
	}
	ae350_drm_copy_fb(drm, plane_state);
	if (ae350_drm_present(drm, 0))
		drm_err(dev, "first present failed\n");
}

static void ae350_drm_pipe_disable(struct drm_simple_display_pipe *pipe)
{
	struct ae350_drm *drm = pipe->crtc.dev->dev_private;
	int ret;

	ret = ae350_video_enable(drm->csr, 0);
	if (ret) {
		drm_err(&drm->dev, "scanout disable failed: %d\n", ret);
		return;
	}

	if (drm->cpu) {
		dma_free_coherent(drm->dev.dev, drm->size, drm->cpu, drm->dma);
		drm->cpu = NULL;
		drm->size = 0;
		drm->width = 0;
		drm->height = 0;
	}
}

static void ae350_drm_pipe_update(struct drm_simple_display_pipe *pipe,
				  struct drm_plane_state *old_plane_state)
{
	struct ae350_drm *drm = pipe->crtc.dev->dev_private;
	struct drm_plane_state *plane_state = pipe->plane.state;

	if (!drm->cpu)
		return;

	if (plane_state && plane_state->fb) {
		ae350_drm_copy_fb(drm, plane_state);
		if (ae350_drm_present(drm, 0))
			drm_err(&drm->dev, "frame present failed\n");
	}
}

static int ae350_drm_pipe_check(struct drm_simple_display_pipe *pipe,
				struct drm_plane_state *plane_state,
				struct drm_crtc_state *crtc_state)
{
	struct ae350_drm *drm = pipe->crtc.dev->dev_private;
	struct drm_framebuffer *fb = plane_state->fb;

	/* No vblank IRQ exists; the atomic core fakes the event. */
	crtc_state->no_vblank = true;

	if (fb) {
		int format_id = ae350_drm_format_id(fb->format->format);
		u32 bpp;

		if (format_id < 0)
			return -EINVAL;
		bpp = ae350_drm_bpp(format_id);
		if (((crtc_state->mode.hdisplay * bpp) & 31u) != 0)
			return -EINVAL;
		if ((plane_state->src_x & 0xffff) || (plane_state->src_y & 0xffff))
			return -EINVAL;
		if (drm->cpu && (drm->width != (u32)crtc_state->mode.hdisplay ||
				 drm->height != (u32)crtc_state->mode.vdisplay ||
				 drm->format_id != (u32)format_id))
			crtc_state->mode_changed = true;
	}
	return 0;
}

static const struct drm_simple_display_pipe_funcs ae350_drm_pipe_funcs = {
	.enable = ae350_drm_pipe_enable,
	.disable = ae350_drm_pipe_disable,
	.update = ae350_drm_pipe_update,
	.check = ae350_drm_pipe_check,
};

static enum drm_mode_status ae350_drm_mode_valid(struct drm_device *dev,
						 const struct drm_display_mode *mode)
{
	struct ae350_drm *drm = dev->dev_private;
	struct ae350_mode_info info;
	struct ae350_pll_result pll;
	u32 h_total, v_total, i, count;
	u32 h_back, v_front, v_sync, v_back;

	if (mode->hdisplay < 1 || mode->hdisplay > 4095 ||
	    mode->vdisplay < 1 || mode->vdisplay > 4095)
		return MODE_BAD;
	h_total = mode->htotal;
	v_total = mode->vtotal;
	if (h_total >= 4096 || v_total >= 4096)
		return MODE_BAD;
	if (mode->clock == 0 || mode->clock > 74250) /* kHz */
		return MODE_BAD;
	count = readl(drm->csr + AE350_V_MODE_COUNT);
	for (i = 0; i < count; i++) {
		ae350_read_mode_info(drm->csr, i, &info);
		if (ae350_drm_match_mode(mode, &info))
			return MODE_OK;
	}
	/* Custom mode validation: width must be aligned such that at least one supported
	 * format (up to 4 bytes per pixel) can produce a 32-byte aligned scanout row. */
	if (((mode->hdisplay * 4) & 31) != 0)
		return MODE_BAD_WIDTH;
	h_back = mode->htotal - mode->hsync_end;
	v_front = mode->vsync_start - mode->vdisplay;
	v_sync = mode->vsync_end - mode->vsync_start;
	v_back = mode->vtotal - mode->vsync_end;
	if (h_back > 255 || v_front > 255 || v_sync > 255 || v_back > 255)
		return MODE_BAD;
	return ae350_video_pll_search(mode->clock * 1000u, &pll) ?
		MODE_BAD : MODE_OK;
}

static int ae350_drm_get_modes(struct drm_connector *connector)
{
	struct ae350_drm *drm = connector->dev->dev_private;
	struct drm_display_mode *mode;
	struct ae350_mode_info info;
	u32 count, i;
	int n = 0;

	count = readl(drm->csr + AE350_V_MODE_COUNT);
	for (i = 0; i < count; i++) {
		ae350_read_mode_info(drm->csr, i, &info);
		mode = drm_mode_create(&drm->dev);
		if (!mode)
			return -ENOMEM;
		mode->hdisplay = info.width;
		mode->vdisplay = info.height;
		mode->hsync_start = info.width + info.h_front;
		mode->hsync_end = mode->hsync_start + info.h_sync;
		mode->htotal = info.h_total;
		mode->vsync_start = info.height + info.v_front;
		mode->vsync_end = mode->vsync_start + info.v_sync;
		mode->vtotal = info.v_total;
		mode->clock = DIV_ROUND_UP(info.actual_pixel_hz, 1000);
		if (info.h_positive)
			mode->flags |= DRM_MODE_FLAG_PHSYNC;
		if (info.v_positive)
			mode->flags |= DRM_MODE_FLAG_PVSYNC;
		drm_mode_set_name(mode);
		drm_mode_probed_add(connector, mode);
		n++;
	}
	return n;
}

static const struct drm_connector_helper_funcs ae350_drm_connector_helper = {
	.get_modes = ae350_drm_get_modes,
};

static enum drm_connector_status ae350_drm_detect(
	struct drm_connector *connector, bool force)
{
	/* Fixed display: always describe as connected so userspace can probe
	 * modes without a sink. Actual HPD state is in sysfs (drm status). */
	return connector_status_connected;
}

static const struct drm_connector_funcs ae350_drm_connector_funcs = {
	.detect = ae350_drm_detect,
	.fill_modes = drm_helper_probe_single_connector_modes,
	.destroy = drm_connector_cleanup,
	.reset = drm_atomic_helper_connector_reset,
	.atomic_duplicate_state = drm_atomic_helper_connector_duplicate_state,
	.atomic_destroy_state = drm_atomic_helper_connector_destroy_state,
};

static const struct drm_mode_config_funcs ae350_drm_mode_funcs = {
	.fb_create = drm_gem_fb_create_with_dirty,
	.mode_valid = ae350_drm_mode_valid,
	.atomic_check = drm_atomic_helper_check,
	.atomic_commit = drm_atomic_helper_commit,
};

DEFINE_DRM_GEM_FOPS(ae350_drm_fops);

static struct drm_driver ae350_drm_driver = {
	DRM_GEM_DMA_DRIVER_OPS_VMAP,
	.name = DRIVER_NAME,
	.desc = DRIVER_DESC,
	.date = DRIVER_DATE,
	.major = DRIVER_MAJOR,
	.minor = DRIVER_MINOR,
	.driver_features = DRIVER_ATOMIC | DRIVER_GEM | DRIVER_MODESET,
	.fops = &ae350_drm_fops,
};

static int ae350_drm_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct ae350_drm *drm;
	struct drm_device *dev_drm;
	struct drm_connector *connector;
	struct resource *res;
	int ret;

	if (!dev_is_dma_coherent(dev))
		return dev_err_probe(dev, -EINVAL, "video requires coherent DMA\n");
	ret = dma_set_mask_and_coherent(dev, DMA_BIT_MASK(32));
	if (ret)
		return ret;

	drm = devm_drm_dev_alloc(dev, &ae350_drm_driver, struct ae350_drm,
				 dev);
	if (IS_ERR(drm))
		return PTR_ERR(drm);
	dev_drm = &drm->dev;
	dev_drm->dev_private = drm;
	platform_set_drvdata(pdev, drm);

	res = platform_get_resource(pdev, IORESOURCE_MEM, 0);
	drm->csr = devm_ioremap_resource(dev, res);
	if (IS_ERR(drm->csr)) {
		ret = PTR_ERR(drm->csr);
		goto release;
	}
	if (readl(drm->csr) != AE350_VIDEO_ID) {
		ret = -ENODEV;
		goto release;
	}
	res = platform_get_resource(pdev, IORESOURCE_MEM, 1);
	if (res) {
		drm->gfx = devm_ioremap_resource(dev, res);
		if (IS_ERR(drm->gfx))
			drm->gfx = NULL;
	}
	ret = drmm_mode_config_init(dev_drm);
	if (ret)
		goto release;
	dev_drm->mode_config.min_width = 1;
	dev_drm->mode_config.max_width = 4096;
	dev_drm->mode_config.min_height = 1;
	dev_drm->mode_config.max_height = 4096;
	dev_drm->mode_config.preferred_depth = 16;
	dev_drm->mode_config.funcs = &ae350_drm_mode_funcs;
	ret = drmm_connector_init(dev_drm, &drm->connector,
				  &ae350_drm_connector_funcs,
				  DRM_MODE_CONNECTOR_HDMIA, NULL);
	if (ret)
		goto release;
	drm_connector_helper_add(&drm->connector, &ae350_drm_connector_helper);
	ret = drm_simple_display_pipe_init(dev_drm, &drm->pipe,
					   &ae350_drm_pipe_funcs,
					   ae350_drm_formats,
					   ARRAY_SIZE(ae350_drm_formats), NULL,
					   &drm->connector);
	if (ret)
		goto release;
	drm_plane_enable_fb_damage_clips(&drm->pipe.plane);
	drm_mode_config_reset(dev_drm);
	ret = drm_dev_register(dev_drm, 0);
	if (ret)
		goto release;
	platform_set_drvdata(pdev, drm);
	drm_fbdev_dma_setup(dev_drm, 16);
	dev_info(dev, "fb%d: KMS %s modes from %u gateware modes\n",
		 dev_drm->primary->index, DRIVER_NAME,
		 readl(drm->csr + AE350_V_MODE_COUNT));
	return 0;

release:
	return ret;
}

static void ae350_drm_remove(struct platform_device *pdev)
{
	struct ae350_drm *drm = platform_get_drvdata(pdev);
	struct drm_device *dev_drm = &drm->dev;

	drm_dev_unplug(dev_drm);
	drm_atomic_helper_shutdown(dev_drm);
}

static void ae350_drm_shutdown(struct platform_device *pdev)
{
	struct ae350_drm *drm = platform_get_drvdata(pdev);

	drm_atomic_helper_shutdown(&drm->dev);
}

static const struct of_device_id ae350_drm_of_match[] = {
	{ .compatible = "riscv-mini,ae350-video-drm" }, { }
};
MODULE_DEVICE_TABLE(of, ae350_drm_of_match);

static struct platform_driver ae350_drm_platform_driver = {
	.probe = ae350_drm_probe,
	.remove = ae350_drm_remove,
	.shutdown = ae350_drm_shutdown,
	.driver = { .name = "ae350-video-drm",
		    .of_match_table = ae350_drm_of_match,
		    .suppress_bind_attrs = true },
};
module_platform_driver(ae350_drm_platform_driver);
MODULE_LICENSE("GPL");
