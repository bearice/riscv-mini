// SPDX-License-Identifier: GPL-2.0
/* Read-only SPI NOR XIP window. The BIOS handoff may leave XIP disabled,
 * so claim/enable its controller before issuing any mapped flash read. */
#include <linux/iopoll.h>
#include <linux/module.h>
#include <linux/mtd/mtd.h>
#include <linux/of.h>
#include <linux/platform_device.h>
struct mini_flash { struct mtd_info mtd; void __iomem *window,*ctrl; };
static int mini_flash_read(struct mtd_info *mtd,loff_t from,size_t len,size_t *retlen,u_char *buf)
{
	struct mini_flash *p=container_of(mtd,struct mini_flash,mtd);
	memcpy_fromio(buf,p->window+from,len); *retlen=len; return 0;
}
static int mini_flash_probe(struct platform_device *pdev)
{
	struct mini_flash *p=devm_kzalloc(&pdev->dev,sizeof(*p),GFP_KERNEL);
	struct resource *res; u32 busy; int ret;
	if(!p) return -ENOMEM;
	p->ctrl=devm_platform_ioremap_resource(pdev,1);
	p->window=devm_platform_get_and_ioremap_resource(pdev,0,&res);
	if(IS_ERR(p->ctrl)||IS_ERR(p->window)) return -ENODEV;
	ret=readl_poll_timeout(p->ctrl+4,busy,!busy,10,100000); if(ret) return ret;
	writel(1,p->ctrl);
	p->mtd=(struct mtd_info){.name="mini-flash-ro",.type=MTD_ROM,.flags=MTD_CAP_ROM|MTD_NO_ERASE,
		.size=resource_size(res),.writesize=1,.owner=THIS_MODULE,._read=mini_flash_read};
	p->mtd.dev.parent=&pdev->dev;
	return mtd_device_register(&p->mtd,NULL,0);
}
static const struct of_device_id mini_flash_match[]={ {.compatible="riscv-mini,flash-xip-ro"},{} };
static struct platform_driver mini_flash_driver={.probe=mini_flash_probe,
	.driver={.name="mini-flash",.of_match_table=mini_flash_match,.suppress_bind_attrs=true}};
builtin_platform_driver(mini_flash_driver);
