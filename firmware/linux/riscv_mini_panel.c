// SPDX-License-Identifier: GPL-2.0
/* Dock ST7789 panel, 240x135 with the board's (40,53) RAM window. */
#include <linux/delay.h>
#include <linux/fb.h>
#include <linux/gpio/consumer.h>
#include <linux/module.h>
#include <linux/spi/spi.h>
#include <linux/vmalloc.h>
#include "riscv_mini_panel_init.h"
#define WIDTH 240
#define HEIGHT 135
#define BYTES (WIDTH*HEIGHT*2)
struct mini_panel {
	struct spi_device *spi;
	struct fb_info *info;
	struct gpio_desc *dc,*reset,*backlight;
	struct fb_deferred_io defio;
	u8 *tx;
	u32 palette[16],updates;
};
static int panel_byte(struct mini_panel *p,bool dc,u8 value)
{ gpiod_set_value_cansleep(p->dc,dc); return spi_write(p->spi,&value,1); }
static void panel_refresh(struct fb_info *info,struct list_head *pages)
{
	struct mini_panel *p=info->par;
	const u16 *pixels=(void *)info->screen_buffer;
	const u16 window[]={0x2a,0x100,0x128,0x101,0x117,0x2b,0x100,0x135,0x100,0x1bb,0x2c};
	int i,ret;
	for(i=0;i<ARRAY_SIZE(window);i++) {
		ret=panel_byte(p,window[i]>>8,window[i]); if(ret) goto error;
	}
	for(i=0;i<WIDTH*HEIGHT;i++) { p->tx[2*i]=pixels[i]>>8; p->tx[2*i+1]=pixels[i]; }
	gpiod_set_value_cansleep(p->dc,1); ret=spi_write(p->spi,p->tx,BYTES);
	if(ret) goto error;
	p->updates++; return;
error:
	dev_err_ratelimited(&p->spi->dev,"panel SPI transfer: %d\n",ret);
}
static void panel_damage_range(struct fb_info *info,off_t off,size_t len)
{ schedule_delayed_work(&info->deferred_work,info->fbdefio->delay); }
static void panel_damage_area(struct fb_info *info,u32 x,u32 y,u32 width,u32 height)
{ schedule_delayed_work(&info->deferred_work,info->fbdefio->delay); }
FB_GEN_DEFAULT_DEFERRED_SYSMEM_OPS(panel,panel_damage_range,panel_damage_area)
static int panel_check(struct fb_var_screeninfo *v,struct fb_info *info)
{ return v->xres==WIDTH && v->yres==HEIGHT && v->xres_virtual==WIDTH && v->yres_virtual==HEIGHT && v->bits_per_pixel==16 && !v->xoffset && !v->yoffset?0:-EINVAL; }
static int panel_color(unsigned int reg,unsigned int r,unsigned int g,unsigned int b,unsigned int t,struct fb_info *info)
{ struct mini_panel *p=info->par; if(reg>=16) return -EINVAL; p->palette[reg]=((r>>11)<<11)|((g>>10)<<5)|(b>>11); return 0; }
static int panel_blank(int blank,struct fb_info *info)
{ struct mini_panel *p=info->par; gpiod_set_value_cansleep(p->backlight,blank==FB_BLANK_UNBLANK); return 0; }
static const struct fb_ops panel_ops={.owner=THIS_MODULE,FB_DEFAULT_DEFERRED_OPS(panel),
	.fb_check_var=panel_check,.fb_setcolreg=panel_color,.fb_blank=panel_blank};
static ssize_t updates_show(struct device *dev,struct device_attribute *attr,char *buf)
{ return sysfs_emit(buf,"%u\n",((struct mini_panel *)dev_get_drvdata(dev))->updates); }
static DEVICE_ATTR_RO(updates);
static int panel_probe(struct spi_device *spi)
{
	struct fb_info *info=framebuffer_alloc(sizeof(struct mini_panel),&spi->dev);
	struct mini_panel *p; int ret,i;
	if(!info) return -ENOMEM;
	p=info->par; p->info=info; p->spi=spi;
	p->dc=devm_gpiod_get(&spi->dev,"dc",GPIOD_OUT_LOW);
	p->reset=devm_gpiod_get(&spi->dev,"reset",GPIOD_OUT_HIGH);
	p->backlight=devm_gpiod_get(&spi->dev,"backlight",GPIOD_OUT_LOW);
	if(IS_ERR(p->dc)||IS_ERR(p->reset)||IS_ERR(p->backlight)) {
		ret=IS_ERR(p->dc)?PTR_ERR(p->dc):IS_ERR(p->reset)?PTR_ERR(p->reset):PTR_ERR(p->backlight); goto free;
	}
	info->screen_buffer=vzalloc(BYTES); p->tx=devm_kmalloc(&spi->dev,BYTES,GFP_KERNEL);
	if(!info->screen_buffer || !p->tx) { ret=-ENOMEM; goto buffer; }
	gpiod_set_value_cansleep(p->reset,1); msleep(100); gpiod_set_value_cansleep(p->reset,0); msleep(200);
	ret=panel_byte(p,0,0x11); if(ret) goto buffer; msleep(120);
	for(i=0;i<ARRAY_SIZE(lcd_init);i++) { ret=panel_byte(p,lcd_init[i]>>8,lcd_init[i]); if(ret) goto buffer; }
	info->fix=(struct fb_fix_screeninfo){.id="mini-spi-lcd",.smem_len=BYTES,.type=FB_TYPE_PACKED_PIXELS,
		.visual=FB_VISUAL_TRUECOLOR,.line_length=WIDTH*2};
	info->var=(struct fb_var_screeninfo){.xres=WIDTH,.yres=HEIGHT,.xres_virtual=WIDTH,.yres_virtual=HEIGHT,
		.bits_per_pixel=16,.red={11,5,0},.green={5,6,0},.blue={0,5,0}};
	info->screen_size=BYTES; info->flags=FBINFO_VIRTFB; info->fbops=&panel_ops; info->pseudo_palette=p->palette;
	p->defio.delay=HZ/2; p->defio.deferred_io=panel_refresh; info->fbdefio=&p->defio;
	ret=fb_deferred_io_init(info); if(ret) goto buffer;
	ret=register_framebuffer(info); if(ret) goto defio;
	spi_set_drvdata(spi,p); gpiod_set_value_cansleep(p->backlight,1);
	panel_refresh(info,NULL); ret=device_create_file(&spi->dev,&dev_attr_updates);
	if(!ret) return 0;
	unregister_framebuffer(info);
defio:
	fb_deferred_io_cleanup(info);
buffer:
	vfree(info->screen_buffer);
free:
	framebuffer_release(info); return ret;
}
static void panel_shutdown(struct spi_device *spi)
{ struct mini_panel *p=spi_get_drvdata(spi); cancel_delayed_work_sync(&p->info->deferred_work); gpiod_set_value_cansleep(p->backlight,0); }
static const struct of_device_id panel_match[]={ {.compatible="riscv-mini,st7789-dock"},{} };
static const struct spi_device_id panel_ids[]={ {"st7789-dock",0},{} };
static struct spi_driver panel_driver={.probe=panel_probe,.shutdown=panel_shutdown,
	.id_table=panel_ids,.driver={.name="mini-panel",.of_match_table=panel_match,.suppress_bind_attrs=true}};
static int __init panel_init(void) { return spi_register_driver(&panel_driver); }
device_initcall(panel_init);
