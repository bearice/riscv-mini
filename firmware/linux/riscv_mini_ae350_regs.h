// SPDX-License-Identifier: GPL-2.0-only
/*
 * Shared register helpers for the AE350 video + GFX2D blocks.
 *
 * Register offsets are the ABI from gateware/machines/mega138k_ae350/
 * address_map.py (VIDEO_REGS / GFX2D_REGS).  Offset 0x00 is the ID
 * register (0xae350501 video, 0xae350601 gfx2d) and 0x04 the ABI version.
 */
#ifndef _RISCV_MINI_AE350_REGS_H
#define _RISCV_MINI_AE350_REGS_H

#include <linux/delay.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/math64.h>
#include <linux/types.h>

#define AE350_VIDEO_ID		0xae350501
#define AE350_GFX2D_ID		0xae350601

enum ae350_video_reg {
	AE350_V_CONTROL		= 12,
	AE350_V_STATUS		= 16,
	AE350_V_IRQ_STATUS	= 20,
	AE350_V_IRQ_ENABLE	= 24,
	AE350_V_LAST_ERROR	= 28,
	AE350_V_BASE0		= 32,
	AE350_V_BASE1		= 36,
	AE350_V_STRIDE		= 40,
	AE350_V_COMMIT		= 44,
	AE350_V_FRAMES		= 48,
	AE350_V_UNDERFLOWS	= 52,
	AE350_V_ADDRESS_ERROR	= 56,
	AE350_V_TIMEOUT_ERROR	= 60,
	AE350_V_ACTIVE_BASE	= 64,
	AE350_V_PIXEL_HZ		= 68,
	AE350_V_GEOMETRY		= 72,
	AE350_V_EPOCH		= 76,
	AE350_V_FIFO_MIN		= 80,
	AE350_V_MAX_WAIT		= 84,
	AE350_V_READS		= 88,
	AE350_V_CPU_COMMANDS	= 92,
	AE350_V_MISSING		= 96,
	AE350_V_SEQUENCE		= 100,
	AE350_V_ACTIVE_SEQUENCE	= 104,
	AE350_V_CANCELLED		= 108,
	AE350_V_ERRORS		= 112,
	AE350_V_CLEAR_ERRORS	= 116,
	AE350_V_REQUEST_OVERFLOW	= 120,
	AE350_V_MODE		= 124,
	AE350_V_FORMAT		= 128,
	AE350_V_MODE_CONFIG	= 132,
	AE350_V_MODE_STATUS	= 136,
	AE350_V_MODE_COUNT	= 140,
	AE350_V_ENABLE		= 144,
	AE350_V_HPD		= 148,
	AE350_V_CUS_WH		= 152,
	AE350_V_CUS_H		= 156,
	AE350_V_CUS_V		= 160,
	AE350_V_CUS_PLL		= 164,
	AE350_V_CUS_PCLK		= 168,
	AE350_V_WRAP_START		= 172,
	AE350_V_WRAP_END		= 176,
	AE350_V_PALETTE_ADDR		= 180,
	AE350_V_PALETTE_DATA		= 184,
	AE350_V_PALETTE_READBACK	= 188,
	AE350_V_PALETTE_ACTIVE		= 192,
	AE350_V_MODE_INFO_BASE	= 256,
};

enum ae350_gfx2d_reg {
	AE350_G_CONTROL		= 12,
	AE350_G_STATUS		= 16,
	AE350_G_IRQ_STATUS	= 20,
	AE350_G_IRQ_ENABLE	= 24,
	AE350_G_LAST_ERROR	= 28,
	AE350_G_OP		= 32,
	AE350_G_SRC_ADDR	= 36,
	AE350_G_DST_ADDR	= 40,
	AE350_G_SRC_STRIDE	= 44,
	AE350_G_DST_STRIDE	= 48,
	AE350_G_WIDTH		= 52,
	AE350_G_HEIGHT		= 56,
	AE350_G_FG_COLOR	= 60,
	AE350_G_BG_COLOR	= 64,
	AE350_G_BLIT_SRC_WB	= 68,
	AE350_G_COMMANDS	= 72,
	AE350_G_ERRORS		= 76,
	AE350_G_CLEAR_ERRORS	= 80,
	AE350_G_FORMAT		= 84,
};

/* GFX2D op codes (docs/platforms/mega138k_ae350/gfx2d.md). */
#define AE350_GFX_FILL	0
#define AE350_GFX_COPY	1
#define AE350_GFX_BLIT	2

/* Video pixel formats (MODE_CONFIG[15:8]). */
#define AE350_FMT_RGB565	0
#define AE350_FMT_RGB888	1
#define AE350_FMT_XRGB8888	2
#define AE350_FMT_XRGB1555	3
#define AE350_FMT_INDEX8	4

#define AE350_VIDEO_MODE_CUSTOM	8u

enum ae350_video_error {
	AE350_VERR_OK		= 0,
	AE350_VERR_ALIGN	= 1,
	AE350_VERR_RANGE	= 2,
	AE350_VERR_BUSY		= 3,
	AE350_VERR_TIMEOUT	= 4,
	AE350_VERR_DEVICE	= 5,
	AE350_VERR_STATE	= 6,
};

struct ae350_mode_info {
	u32 width;
	u32 height;
	u32 actual_pixel_hz;
	u32 h_total;
	u32 v_total;
	u32 h_front;
	u32 h_sync;
	u32 h_back;
	u32 v_front;
	u32 v_sync;
	u32 v_back;
	u32 h_positive;
	u32 v_positive;
	u32 refresh_millihz;
	u32 nominal_pixel_hz;
};

struct ae350_pll_result {
	u32 idiv;
	u32 m8;
	u32 pixdiv;
	u32 serdiv;
};

/*
 * PLL search mirroring gateware/machines/mega138k_ae350/video_modes.py
 * pll_config() and the BSP ae350_video_set_custom_mode(): integer
 * dividers preserve the exact serializer 5:1 ratio, MDIV has eighth
 * steps, VCO must stay within 650 MHz..1.3 GHz, error within 0.5%.
 */
static inline int ae350_video_pll_search(u32 pixel_hz,
					 struct ae350_pll_result *out)
{
	int best = -1;
	u32 best_idiv = 0, best_m8 = 0, best_ser = 0;
	u32 idiv, ser;

	for (idiv = 1; idiv <= 2; idiv++) {
		for (ser = 2; ser <= 25; ser++) {
			u32 pixdiv = ser * 5u;
			u64 m8 = div_u64((u64)pixel_hz * pixdiv * idiv * 8u +
					 25000000u, 50000000);
			u64 vco = (u64)50000000u * m8;
			u64 lo = (u64)650000000u * idiv * 8u;
			u64 hi = (u64)1300000000u * idiv * 8u;
			u64 scaled = (u64)pixel_hz * idiv * 8u * pixdiv;
			u64 tol = div_u64(scaled, 200); /* 0.5% */
			s64 err;

			if (m8 < 16 || m8 > 1023 || vco < lo || vco > hi)
				continue;
			err = (s64)vco - (s64)scaled;
			if (err < 0)
				err = -err;
			if ((u64)err > tol)
				continue;
			if (best < 0 || err < best) {
				best = (int)err;
				best_idiv = idiv;
				best_m8 = (u32)m8;
				best_ser = ser;
			}
		}
	}
	if (best < 0)
		return -ERANGE;
	out->idiv = best_idiv;
	out->m8 = best_m8;
	out->pixdiv = best_ser * 5u;
	out->serdiv = best_ser;
	return 0;
}

static inline void ae350_read_mode_info(void __iomem *csr, u32 index,
					struct ae350_mode_info *info)
{
	u32 base = AE350_V_MODE_INFO_BASE + index * 32u;
	u32 w0 = readl(csr + base + 0);
	u32 w2 = readl(csr + base + 8);
	u32 w3 = readl(csr + base + 12);
	u32 w4 = readl(csr + base + 16);
	u32 w5 = readl(csr + base + 20);

	info->width = w0 & 0xffffu;
	info->height = (w0 >> 16) & 0xffffu;
	info->actual_pixel_hz = readl(csr + base + 4);
	info->h_total = w2 & 0xffffu;
	info->v_total = (w2 >> 16) & 0xffffu;
	info->h_front = w3 & 0x3ffu;
	info->h_sync = (w3 >> 10) & 0x3ffu;
	info->h_back = (w3 >> 20) & 0xfffu;
	info->v_front = w4 & 0x3ffu;
	info->v_sync = (w4 >> 10) & 0x3ffu;
	info->v_back = (w4 >> 20) & 0xfffu;
	info->h_positive = w5 & 1u;
	info->v_positive = (w5 >> 1) & 1u;
	info->refresh_millihz = readl(csr + base + 24);
	info->nominal_pixel_hz = readl(csr + base + 28);
}

/* STATUS bits: bit4 = scanout stopped, bit1 = active slot. */
#define AE350_V_STATUS_STOPPED	16u

static inline int ae350_video_wait_commit(void __iomem *csr)
{
	u32 v;
	return readl_poll_timeout(csr + AE350_V_COMMIT, v, !(v & 1),
				  1000, 200000);
}

static inline int ae350_video_commit(void __iomem *csr, u32 control)
{
	u32 sequence = readl(csr + AE350_V_SEQUENCE) + 1u;
	int ret;

	ret = ae350_video_wait_commit(csr);
	if (ret)
		return AE350_VERR_TIMEOUT;
	writel(control, csr + AE350_V_CONTROL);
	writel(1, csr + AE350_V_COMMIT);
	ret = ae350_video_wait_commit(csr);
	if (ret)
		return AE350_VERR_TIMEOUT;
	return readl(csr + AE350_V_ACTIVE_SEQUENCE) == sequence ?
		AE350_VERR_OK : AE350_VERR_STATE;
}

static inline int ae350_video_stop(void __iomem *csr)
{
	int ret;

	ret = ae350_video_commit(csr, 0);
	if (ret)
		return ret;
	/* Wait for the scanout to report stopped. */
	{
		u32 v;
		ret = readl_poll_timeout(csr + AE350_V_STATUS, v,
					 (v & AE350_V_STATUS_STOPPED),
					 1000, 200000);
	}
	return ret ? AE350_VERR_TIMEOUT : AE350_VERR_OK;
}

static inline int ae350_video_enable(void __iomem *csr, u32 enable)
{
	if (enable > 1)
		return AE350_VERR_RANGE;
	if (readl(csr + AE350_V_MODE_STATUS) || readl(csr + AE350_V_COMMIT))
		return AE350_VERR_BUSY;
	if (!enable) {
		int ret = ae350_video_stop(csr);
		if (ret)
			return ret;
	} else if (!(readl(csr + AE350_V_STATUS) & AE350_V_STATUS_STOPPED)) {
		return AE350_VERR_BUSY;
	}
	writel(enable, csr + AE350_V_ENABLE);
	return readl(csr + AE350_V_ENABLE) == enable ?
		AE350_VERR_OK : AE350_VERR_STATE;
}

static inline int ae350_video_set_mode(void __iomem *csr, u32 mode, u32 format)
{
	int ret;

	if (format > 3)
		return AE350_VERR_RANGE;
	ret = ae350_video_enable(csr, 0);
	if (ret)
		return ret;
	writel(mode | (format << 8), csr + AE350_V_MODE_CONFIG);
	{
		u32 v;
		ret = readl_poll_timeout(csr + AE350_V_MODE_STATUS, v, !(v & 1),
					 1000, 200000);
	}
	if (ret)
		return AE350_VERR_TIMEOUT;
	return readl(csr + AE350_V_MODE) == mode &&
		readl(csr + AE350_V_FORMAT) == format ?
		AE350_VERR_OK : AE350_VERR_STATE;
}

static inline int ae350_video_set_custom_mode(
	void __iomem *csr, const struct ae350_mode_info *mode, u32 format,
	const struct ae350_pll_result *pll, u32 pixel_hz)
{
	static const u8 bpp_table[4] = { 2, 3, 4, 2 };
	u32 bpp = bpp_table[format & 3];
	u32 h_total = mode->width + mode->h_front + mode->h_sync + mode->h_back;
	u32 v_total = mode->height + mode->v_front + mode->v_sync + mode->v_back;
	int ret;

	if (format > 3 || mode->h_positive > 1 || mode->v_positive > 1 ||
	    mode->h_back > 255 || mode->v_front > 255 || mode->v_sync > 255 ||
	    mode->v_back > 255 || !mode->width || !mode->height ||
	    ((mode->width * bpp) & 31u) != 0 ||
	    h_total >= 4096 || v_total >= 4096 || mode->width > 4095 ||
	    mode->height > 4095 || mode->h_front > 4095 || mode->h_sync > 4095)
		return AE350_VERR_RANGE;
	ret = ae350_video_enable(csr, 0);
	if (ret)
		return ret;
	writel(mode->width | ((mode->height & 4095u) << 16), csr + AE350_V_CUS_WH);
	writel(mode->h_front | ((mode->h_sync & 4095u) << 12) |
	       ((mode->h_back & 255u) << 24), csr + AE350_V_CUS_H);
	writel(mode->v_front | ((mode->v_sync & 255u) << 8) |
	       ((mode->v_back & 255u) << 16) |
	       ((mode->h_positive & 1u) << 24) |
	       ((mode->v_positive & 1u) << 25), csr + AE350_V_CUS_V);
	writel(pll->idiv | ((pll->m8 & 1023u) << 2) |
	       ((pll->pixdiv & 127u) << 12) | ((pll->serdiv & 127u) << 19),
	       csr + AE350_V_CUS_PLL);
	writel(pixel_hz, csr + AE350_V_CUS_PCLK);
	writel(AE350_VIDEO_MODE_CUSTOM | (format << 8), csr + AE350_V_MODE_CONFIG);
	{
		u32 v;
		ret = readl_poll_timeout(csr + AE350_V_MODE_STATUS, v, !(v & 1),
					 1000, 200000);
	}
	if (ret)
		return AE350_VERR_TIMEOUT;
	return readl(csr + AE350_V_MODE) == AE350_VIDEO_MODE_CUSTOM &&
		readl(csr + AE350_V_FORMAT) == format ?
		AE350_VERR_OK : AE350_VERR_STATE;
}

/* D-cache clean through CSRs. The kernel runs in S-mode, so the
 * M-mode mcctlbeginaddr/mcctlcommand (0x7cb/0x7cc) are illegal there;
 * use the S/U twins ucctlbeginaddr/ucctlcommand (0x80b/0x80c),
 * which require CCTL_SUEN (bit 8 of mcache_ctl) set by M-mode
 * firmware (BIOS runtime init). Command 1 = L1D_VA_WB clean. */
static inline void ae350_cache_clean_range(u32 addr, u32 bytes)
{
	u32 end = addr + bytes;

	addr &= ~31u;
	while (addr < end) {
		asm volatile("csrw 0x80b, %0" :: "r"(addr));
		asm volatile("csrw 0x80c, %0" :: "r"(1u));
		addr += 32u;
	}
	asm volatile("fence" ::: "memory");
}

static inline void ae350_cache_inval_range(u32 addr, u32 bytes)
{
	u32 end = addr + bytes;

	addr &= ~31u;
	while (addr < end) {
		asm volatile("csrw 0x80b, %0" :: "r"(addr));
		asm volatile("csrw 0x80c, %0" :: "r"(0u));
		addr += 32u;
	}
	asm volatile("fence" ::: "memory");
}

/* GFX2D helpers. STATUS: bit0 busy, bit1 done, bit2 error. */
#define AE350_G_STATUS_BUSY	1u
#define AE350_G_STATUS_DONE	2u
#define AE350_G_STATUS_ERROR	4u

static inline int ae350_gfx2d_wait(void __iomem *gfx, u32 rounds)
{
	u32 i;

	for (i = 0; i < rounds; i++) {
		u32 st = readl(gfx + AE350_G_STATUS);

		if (st & AE350_G_STATUS_DONE)
			return (st & AE350_G_STATUS_ERROR) ?
				(int)readl(gfx + AE350_G_LAST_ERROR) : 0;
		if (!(st & AE350_G_STATUS_BUSY))
			return AE350_VERR_RANGE; /* not running: PARAM */
		{ unsigned d; for (d = 0; d < 24; d++) { } } /* backoff */
	}
	return AE350_VERR_TIMEOUT;
}

static inline int ae350_gfx2d_run(void __iomem *gfx)
{
	writel(1u, gfx + AE350_G_CONTROL);
	return ae350_gfx2d_wait(gfx, 0x40000);
}

#endif /* _RISCV_MINI_AE350_REGS_H */
