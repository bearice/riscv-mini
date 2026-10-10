// SPDX-License-Identifier: GPL-2.0
#include <linux/gpio/driver.h>
#include <linux/input.h>
#include <linux/interrupt.h>
#include <linux/iopoll.h>
#include <linux/led-class-multicolor.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/timer.h>

struct mini_led { struct led_classdev led; void __iomem *regs; spinlock_t *lock; unsigned int bit; };
struct mini_board {
	void __iomem *regs;
	struct gpio_chip gpio;
	spinlock_t lock;
	struct mini_led leds[6];
	struct input_dev *input;
	struct timer_list timer;
	u32 inputs;
};
static int mini_gpio_get(struct gpio_chip *chip,unsigned int offset)
{
	struct mini_board *b=gpiochip_get_data(chip);
	return offset<6 ? !!(readl(b->regs)&BIT(offset)) :
		!!(readl(b->regs+4)&BIT(offset-6));
}
static void mini_gpio_set(struct gpio_chip *chip,unsigned int offset,int value)
{
	struct mini_board *b=gpiochip_get_data(chip);
	unsigned long flags; u32 bits;
	if(offset>=6) return;
	spin_lock_irqsave(&b->lock,flags); bits=readl(b->regs);
	writel(value?bits|BIT(offset):bits&~BIT(offset),b->regs);
	spin_unlock_irqrestore(&b->lock,flags);
}
static int mini_gpio_input(struct gpio_chip *chip,unsigned int offset) { return offset>=6?0:-EINVAL; }
static int mini_gpio_output(struct gpio_chip *chip,unsigned int offset,int value)
{ if(offset>=6) return -EINVAL; mini_gpio_set(chip,offset,value); return 0; }
static int mini_gpio_direction(struct gpio_chip *chip,unsigned int offset) { return offset>=6; }
static void mini_led_set(struct led_classdev *led,enum led_brightness value)
{
	struct mini_led *l=container_of(led,struct mini_led,led);
	unsigned long flags; u32 bits;
	spin_lock_irqsave(l->lock,flags); bits=readl(l->regs);
	writel(value?bits|BIT(l->bit):bits&~BIT(l->bit),l->regs);
	spin_unlock_irqrestore(l->lock,flags);
}
static void mini_input_report(struct mini_board *b)
{
	u32 inputs=readl(b->regs+4); int i;
	for(i=0;i<4;i++) {
		input_report_key(b->input,BTN_0+i,!!(inputs&BIT(i)));
		input_report_switch(b->input,SW_LID+i,!!(inputs&BIT(i+4)));
	}
	input_sync(b->input); b->inputs=inputs;
	writel(((inputs>>8)&15)|((inputs>>8)&0xf0),b->regs+8);
	writel(1,b->regs+16);
}
static irqreturn_t mini_board_irq(int irq,void *data)
{ struct mini_board *b=data; mini_input_report(b); return IRQ_HANDLED; }
static void mini_board_timer(struct timer_list *timer)
{
	struct mini_board *b=from_timer(b,timer,timer);
	/* Switches have no hardware event latch, so sample them too. */
	mini_input_report(b); mod_timer(&b->timer,jiffies+msecs_to_jiffies(50));
}
static int mini_board_probe(struct platform_device *pdev)
{
	struct mini_board *b; int ret,i,irq;
	b=devm_kzalloc(&pdev->dev,sizeof(*b),GFP_KERNEL); if(!b) return -ENOMEM;
	b->regs=devm_platform_ioremap_resource(pdev,0); if(IS_ERR(b->regs)) return PTR_ERR(b->regs);
	spin_lock_init(&b->lock);
	if(of_device_is_compatible(pdev->dev.of_node,"riscv-mini,lcd-gpio")) {
		b->gpio=(struct gpio_chip){.label="mini-lcd",.parent=&pdev->dev,.owner=THIS_MODULE,
			.base=-1,.ngpio=3,.get=mini_gpio_get,.set=mini_gpio_set,
			.direction_input=mini_gpio_input,.direction_output=mini_gpio_output,.get_direction=mini_gpio_direction};
		platform_set_drvdata(pdev,b);
		return devm_gpiochip_add_data(&pdev->dev,&b->gpio,b);
	}
	writel(0,b->regs+20); writel(255,b->regs+8); writel(1,b->regs+16);
	b->gpio=(struct gpio_chip){.label="mini-board",.parent=&pdev->dev,.owner=THIS_MODULE,
		.base=-1,.ngpio=14,.get=mini_gpio_get,.set=mini_gpio_set,
		.direction_input=mini_gpio_input,.direction_output=mini_gpio_output,.get_direction=mini_gpio_direction};
	ret=devm_gpiochip_add_data(&pdev->dev,&b->gpio,b); if(ret) return ret;
	for(i=0;i<6;i++) {
		b->leds[i].regs=b->regs; b->leds[i].lock=&b->lock; b->leds[i].bit=i;
		b->leds[i].led.name=devm_kasprintf(&pdev->dev,GFP_KERNEL,"mini:green:led%d",i);
		if(!b->leds[i].led.name) return -ENOMEM;
		b->leds[i].led.max_brightness=1; b->leds[i].led.brightness_set=mini_led_set;
		ret=devm_led_classdev_register(&pdev->dev,&b->leds[i].led); if(ret) return ret;
	}
	b->input=devm_input_allocate_device(&pdev->dev); if(!b->input) return -ENOMEM;
	b->input->name="riscv-mini buttons and switches";
	for(i=0;i<4;i++) { input_set_capability(b->input,EV_KEY,BTN_0+i); input_set_capability(b->input,EV_SW,SW_LID+i); }
	ret=input_register_device(b->input); if(ret) return ret;
	irq=platform_get_irq(pdev,0); if(irq<0) return irq;
	ret=devm_request_irq(&pdev->dev,irq,mini_board_irq,0,dev_name(&pdev->dev),b); if(ret) return ret;
	platform_set_drvdata(pdev,b); timer_setup(&b->timer,mini_board_timer,0);
	mini_input_report(b); writel(1,b->regs+20); mod_timer(&b->timer,jiffies+msecs_to_jiffies(50));
	return 0;
}
static void mini_board_shutdown(struct platform_device *pdev)
{ struct mini_board *b=platform_get_drvdata(pdev); if(b->input) { writel(0,b->regs+20); del_timer_sync(&b->timer); } }
struct mini_rgb { struct led_classdev_mc mc; struct mc_subled colors[3]; void __iomem *regs; };
static int mini_rgb_set(struct led_classdev *led,enum led_brightness value)
{
	struct led_classdev_mc *mc=lcdev_to_mccdev(led);
	struct mini_rgb *rgb=container_of(mc,struct mini_rgb,mc);
	u32 busy; int ret;
	ret=readl_poll_timeout(rgb->regs+8,busy,!busy,10,10000); if(ret) return ret;
	led_mc_calc_color_components(mc,value);
	writel((rgb->colors[0].brightness<<16)|(rgb->colors[1].brightness<<8)|rgb->colors[2].brightness,rgb->regs);
	writel(1,rgb->regs+4); return 0;
}
static int mini_rgb_probe(struct platform_device *pdev)
{
	struct mini_rgb *rgb=devm_kzalloc(&pdev->dev,sizeof(*rgb),GFP_KERNEL);
	int i; const unsigned int ids[]={LED_COLOR_ID_RED,LED_COLOR_ID_GREEN,LED_COLOR_ID_BLUE};
	if(!rgb) return -ENOMEM;
	rgb->regs=devm_platform_ioremap_resource(pdev,0); if(IS_ERR(rgb->regs)) return PTR_ERR(rgb->regs);
	for(i=0;i<3;i++) { rgb->colors[i].color_index=ids[i]; rgb->colors[i].intensity=255; }
	rgb->mc.num_colors=3; rgb->mc.subled_info=rgb->colors;
	rgb->mc.led_cdev.name="mini:rgb:status"; rgb->mc.led_cdev.max_brightness=255;
	rgb->mc.led_cdev.brightness_set_blocking=mini_rgb_set;
	return devm_led_classdev_multicolor_register(&pdev->dev,&rgb->mc);
}
static const struct of_device_id mini_board_match[]={ {.compatible="riscv-mini,board-io"},{.compatible="riscv-mini,lcd-gpio"},{} };
static const struct of_device_id mini_rgb_match[]={ {.compatible="riscv-mini,ws2812"},{} };
static struct platform_driver mini_board_driver={.probe=mini_board_probe,.shutdown=mini_board_shutdown,
	.driver={.name="mini-board",.of_match_table=mini_board_match,.suppress_bind_attrs=true}};
static struct platform_driver mini_rgb_driver={.probe=mini_rgb_probe,
	.driver={.name="mini-ws2812",.of_match_table=mini_rgb_match,.suppress_bind_attrs=true}};
static int __init mini_board_init(void)
{
	int ret=platform_driver_register(&mini_board_driver);
	return ret?:platform_driver_register(&mini_rgb_driver);
}
device_initcall(mini_board_init);
