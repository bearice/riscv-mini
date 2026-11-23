/* SPDX-License-Identifier: GPL-2.0 WITH Linux-syscall-note */
#ifndef _AE350_GFX3D_UAPI_H
#define _AE350_GFX3D_UAPI_H

#include <linux/ioctl.h>
#include <linux/types.h>

#define AE350_GFX3D_ABI_VERSION     1u
#define AE350_GFX3D_MAX_W           640u
#define AE350_GFX3D_MAX_H           480u
#define AE350_GFX3D_MAX_BATCH       2048u
#define AE350_GFX3D_TMEM_MAX_TEXELS 4096u

#define AE350_GFX3D_READBACK_RGB565   0u
#define AE350_GFX3D_READBACK_XRGB8888 1u

struct ae350_gfx3d_info {
	__u32 version;
	__u32 max_width;
	__u32 max_height;
	__u32 max_batch;
	__u32 tmem_texels;
	__u32 fb_width;
	__u32 fb_height;
	__u32 fb_format;
};

struct ae350_gfx3d_clear {
	__u32 width;
	__u32 height;
	__u32 clear_color;
	__u32 clear_z;
	__u32 color_xrgb8888;
	__u32 z_val;
};

struct ae350_gfx3d_tex {
	__u32 width;
	__u32 height;
	__u32 bilinear;
	__u32 wrap_u;
	__u32 wrap_v;
	__u32 pad;
	__u64 rgb565_ptr;
};

struct ae350_gfx3d_tri {
	__u32 z_cfg;
	__u32 tex_cfg;
	__u32 words[24];
};

struct ae350_gfx3d_submit {
	__u32 width;
	__u32 height;
	__u32 count;
	__u32 flags;
	__u64 tris_ptr;
	__u32 pixels_out;
	__u32 pad;
};

struct ae350_gfx3d_readback {
	__u32 width;
	__u32 height;
	__u32 dst_stride; /* bytes per row in dst_ptr */
	__u32 fmt;        /* AE350_GFX3D_READBACK_* */
	__u64 dst_ptr;
};

#define AE350_GFX3D_IOC_INFO       _IOR('G', 0x01, struct ae350_gfx3d_info)
#define AE350_GFX3D_IOC_CLEAR      _IOW('G', 0x02, struct ae350_gfx3d_clear)
#define AE350_GFX3D_IOC_UPLOAD_TEX _IOW('G', 0x03, struct ae350_gfx3d_tex)
#define AE350_GFX3D_IOC_SUBMIT     _IOWR('G', 0x04, struct ae350_gfx3d_submit)
#define AE350_GFX3D_IOC_READBACK   _IOW('G', 0x05, struct ae350_gfx3d_readback)

#endif /* _AE350_GFX3D_UAPI_H */
