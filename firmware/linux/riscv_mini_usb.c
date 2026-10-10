// SPDX-License-Identifier: GPL-2.0
/* Full-speed Ultraembedded PIO HCD. One packet per work item; NAKs yield
 * to other endpoints. This IP has no isochronous or low-speed scheduler. */
#include <linux/delay.h>
#include <linux/iopoll.h>
#include <linux/module.h>
#include <linux/platform_device.h>
#include <linux/unaligned.h>
#include <linux/usb.h>
#include <linux/usb/hcd.h>
#include <linux/workqueue.h>
#include "riscv_mini_usb_port.h"

enum { CTRL=0, STATUS=4, IRQ_ACK=8, IRQ_MASK=16, TX_LEN=20,
	TOKEN=24, RX_STAT=28, FIFO=32 };
enum { RUN=0x1e9, RESET=0xc4, SETUP=0x2d, OUT=0xe1, IN=0x69,
	ACK=0xd2, NAK=0x5a, STALL=0x1e, DATA0=0xc3, DATA1=0x4b };
struct mini_urb {
	struct list_head list;
	struct urb *urb;
	unsigned int stage, toggle, retries;
	int cancelled;
	unsigned long ready;
};
struct mini_usb {
	struct usb_hcd *hcd;
	void __iomem *regs, *ctrl, *phyreset;
	spinlock_t lock;
	struct mutex io;
	struct list_head queue;
	struct delayed_work work;
	u16 change;
	struct mini_usb_port port;
	bool enabled, stopping, resetting, transacting;
};
static struct mini_usb *mini_hcd(struct usb_hcd *hcd) { return (void *)hcd->hcd_priv; }
static int mini_packet(struct mini_usb *p,struct urb *urb,bool in,int toggle,int pid,
	const u8 *tx,int len,u8 *rx,int cap,int *count,int *reply)
{
	u32 status; int i,ret,attempt;
	for(attempt=0;attempt<3;attempt++) {
		writel(6,p->regs+IRQ_ACK); writel(RUN,p->regs+CTRL);
		for(i=0;!in && i<len;i++) writel(tx[i],p->regs+FIFO);
		writel(in?0:len,p->regs+TX_LEN);
		writel(BIT(31)|BIT(29)|(in?BIT(30):0)|(toggle?BIT(28):0)|
			(pid<<16)|(usb_pipedevice(urb->pipe)<<9)|(usb_pipeendpoint(urb->pipe)<<5),p->regs+TOKEN);
		usleep_range(1000,1100);
		ret=readl_poll_timeout(p->regs+RX_STAT,status,!(status&BIT(31)) && (status&BIT(28)),10,50000);
		if(ret) return ret;
		writel(6,p->regs+IRQ_ACK); *count=status&0xffff; *reply=(status>>16)&255;
		if(*count>64 || (status&(BIT(29)|BIT(30)))) {
			/* Discard a bounded corrupt packet before retrying. */
			for(i=0;i<min(*count,64);i++) readl(p->regs+FIFO);
			continue;
		}
		for(i=0;i<*count;i++) { u8 byte=readl(p->regs+FIFO); if(in && i<cap && rx) rx[i]=byte; }
		return 0;
	}
	return -EPROTO;
}
/* Returns 1 to retain the URB, zero for completion, negative for error. */
static int mini_step(struct mini_usb *p,struct mini_urb *q)
{
	struct urb *urb=q->urb;
	bool control=usb_pipecontrol(urb->pipe), in=usb_pipein(urb->pipe);
	int mps=usb_endpoint_maxp(&urb->ep->desc), len,cap,count,reply,ret,toggle,pid;
	u8 *buffer=urb->transfer_buffer, scratch[64]; const u8 *tx=NULL; u8 *rx=NULL;
	if(urb->dev->speed!=USB_SPEED_FULL || mps>64 || !mps) return -EOPNOTSUPP;
	len=min(mps,urb->transfer_buffer_length-urb->actual_length); cap=len;
	toggle=control?q->toggle:usb_gettoggle(urb->dev,usb_pipeendpoint(urb->pipe),!in);
	if(control && q->stage==0) { in=false; pid=SETUP; toggle=0; tx=urb->setup_packet; len=8; cap=0; }
	else if(control && q->stage==2) { in=urb->transfer_buffer_length?!in:true; pid=in?IN:OUT; toggle=1; len=0; cap=64; rx=scratch; }
	else { pid=in?IN:OUT; if(in) rx=buffer+urb->actual_length; else tx=buffer+urb->actual_length; }
	ret=mini_packet(p,urb,in,toggle,pid,tx,len,rx,cap,&count,&reply); if(ret) return ret;
	if(reply==STALL) return -EPIPE;
	if(reply==NAK) {
		if(!usb_pipeint(urb->pipe) && ++q->retries>1000) return -ETIMEDOUT;
		q->ready=jiffies+msecs_to_jiffies(usb_pipeint(urb->pipe)?max(urb->interval,1):1);
		return 1;
	}
	if(in) {
		if(reply!=DATA0 && reply!=DATA1) return -EPROTO;
		if(reply!=(toggle?DATA1:DATA0)) { q->ready=jiffies+1; return ++q->retries>10?-EPROTO:1; }
		if(count>cap) return -EOVERFLOW;
	} else if(reply!=ACK) return -EPROTO;
	q->retries=0;
	if(control && q->stage==0) { q->stage=urb->transfer_buffer_length?1:2; q->toggle=1; return 1; }
	if(control && q->stage==2) return count?-EPROTO:0;
	urb->actual_length+=in?count:len;
	if(control) q->toggle^=1;
	else usb_settoggle(urb->dev,usb_pipeendpoint(urb->pipe),!in,toggle^1);
	if(urb->actual_length==urb->transfer_buffer_length || (in && count<mps)) {
		if(in && count<mps && (urb->transfer_flags&URB_SHORT_NOT_OK) && urb->actual_length<urb->transfer_buffer_length) return -EREMOTEIO;
		if(control) { q->stage=2; return 1; }
		/* Emit the requested terminating zero packet once. */
		if(!in && len && !(len%mps) && (urb->transfer_flags&URB_ZERO_PACKET)) {
			q->stage=3; return 1;
		}
		return 0;
	}
	return 1;
}
static void mini_usb_work(struct work_struct *work)
{
	struct mini_usb *p=container_of(to_delayed_work(work),struct mini_usb,work);
	struct mini_urb *q=NULL,*entry; unsigned long flags; int ret;
	spin_lock_irqsave(&p->lock,flags);
	list_for_each_entry(entry,&p->queue,list) {
		if(p->stopping || entry->cancelled || time_after_eq(jiffies,entry->ready)) { q=entry; break; }
	}
	spin_unlock_irqrestore(&p->lock,flags);
	if(q) {
		mutex_lock(&p->io);
		WRITE_ONCE(p->transacting, true);
		ret=READ_ONCE(p->stopping)?-ESHUTDOWN:READ_ONCE(q->cancelled)?q->cancelled:mini_step(p,q);
		WRITE_ONCE(p->transacting, false);
		mutex_unlock(&p->io);
		spin_lock_irqsave(&p->lock,flags);
		if(q->cancelled) ret=q->cancelled;
		if(ret!=1) {
			list_del(&q->list); q->urb->hcpriv=NULL; usb_hcd_unlink_urb_from_ep(p->hcd,q->urb);
		} else list_move_tail(&q->list,&p->queue);
		spin_unlock_irqrestore(&p->lock,flags);
		if(ret!=1) { usb_hcd_giveback_urb(p->hcd,q->urb,ret); kfree(q); }
	}
	spin_lock_irqsave(&p->lock,flags);
	if(!list_empty(&p->queue)) {
		unsigned long delay=1;
		list_for_each_entry(entry,&p->queue,list)
			if(entry->cancelled || time_after_eq(jiffies,entry->ready)) { delay=0; break; }
		mod_delayed_work(system_wq,&p->work,delay);
	}
	spin_unlock_irqrestore(&p->lock,flags);
}
static int mini_enqueue(struct usb_hcd *hcd,struct urb *urb,gfp_t flags)
{
	struct mini_usb *p=mini_hcd(hcd); struct mini_urb *q; unsigned long irqflags; int ret;
	if(usb_pipeisoc(urb->pipe)) return -EOPNOTSUPP;
	q=kzalloc(sizeof(*q),flags); if(!q) return -ENOMEM;
	/* INITIAL_JIFFIES starts before zero; zero would defer early URBs for minutes. */
	q->urb=urb; q->ready=jiffies;
	spin_lock_irqsave(&p->lock,irqflags);
	ret=p->stopping?-ESHUTDOWN:usb_hcd_link_urb_to_ep(hcd,urb);
	if(!ret) { urb->hcpriv=q; list_add_tail(&q->list,&p->queue); mod_delayed_work(system_wq,&p->work,0); }
	spin_unlock_irqrestore(&p->lock,irqflags);
	if(ret) kfree(q); return ret;
}
static int mini_dequeue(struct usb_hcd *hcd,struct urb *urb,int status)
{
	struct mini_usb *p=mini_hcd(hcd); unsigned long flags; int ret;
	spin_lock_irqsave(&p->lock,flags); ret=usb_hcd_check_unlink_urb(hcd,urb,status);
	if(!ret && urb->hcpriv) { ((struct mini_urb *)urb->hcpriv)->cancelled=status; mod_delayed_work(system_wq,&p->work,0); }
	spin_unlock_irqrestore(&p->lock,flags); return ret;
}
static int mini_usb_start(struct usb_hcd *hcd)
{ hcd->state=HC_STATE_RUNNING; hcd->uses_new_polling=1; set_bit(HCD_FLAG_POLL_RH,&hcd->flags); return 0; }
static void mini_usb_stop(struct usb_hcd *hcd)
{
	struct mini_usb *p=mini_hcd(hcd); struct mini_urb *q; unsigned long flags;
	spin_lock_irqsave(&p->lock,flags); p->stopping=true; spin_unlock_irqrestore(&p->lock,flags);
	cancel_delayed_work_sync(&p->work);
	while(!list_empty(&p->queue)) {
		q=list_first_entry(&p->queue,struct mini_urb,list);
		list_del(&q->list); q->urb->hcpriv=NULL; usb_hcd_unlink_urb_from_ep(hcd,q->urb);
		usb_hcd_giveback_urb(hcd,q->urb,-ESHUTDOWN); kfree(q);
	}
	writel(0,p->regs+IRQ_MASK); writel(RESET,p->regs+CTRL); hcd->state=HC_STATE_HALT;
}
static int mini_hub_status(struct usb_hcd *hcd,char *buf)
{
	struct mini_usb *p=mini_hcd(hcd); unsigned long flags; int changed;
	/* Called by the root-hub timer: no sleeping locks here. Reset SE0 and
	 * packet J/K/EOP transitions must not be reported as unplug events. */
	spin_lock_irqsave(&p->lock,flags);
	if (!READ_ONCE(p->resetting) && !READ_ONCE(p->transacting) &&
	    mini_usb_port_sample(&p->port, readl(p->regs+STATUS)&3,
				 jiffies, msecs_to_jiffies(20))) {
		p->change|=USB_PORT_STAT_C_CONNECTION;
		if(!p->port.connected) p->enabled=false;
	}
	changed=p->change?1:0; buf[0]=changed?2:0;
	spin_unlock_irqrestore(&p->lock,flags); return changed;
}
static int mini_hub_control(struct usb_hcd *hcd,u16 type,u16 value,u16 index,char *buf,u16 length)
{
	struct mini_usb *p=mini_hcd(hcd); u32 status; unsigned long flags; int ret=0;
	mutex_lock(&p->io);
	switch(type) {
	case GetHubDescriptor: {
		const u8 descriptor[]={9,0x29,1,0,0,1,0,0,0xff};
		memcpy(buf,descriptor,min_t(unsigned int,length,sizeof(descriptor))); break;
	}
	case GetHubStatus: put_unaligned_le32(0,buf); break;
	case GetPortStatus:
		if(index!=1) { ret=-EPIPE; break; }
		spin_lock_irqsave(&p->lock,flags);
		status=USB_PORT_STAT_POWER;
		if(p->port.connected) status|=USB_PORT_STAT_CONNECTION;
		if(p->enabled) status|=USB_PORT_STAT_ENABLE;
		put_unaligned_le32(status|((u32)p->change<<16),buf);
		spin_unlock_irqrestore(&p->lock,flags); break;
	case SetPortFeature:
		if(index!=1) { ret=-EPIPE; break; }
		if(value==USB_PORT_FEAT_RESET) {
			WRITE_ONCE(p->resetting,true);
			writel(RESET,p->regs+CTRL); msleep(20); writel(RUN,p->regs+CTRL);
			spin_lock_irqsave(&p->lock,flags);
			p->enabled=true; p->change|=USB_PORT_STAT_C_RESET;
			WRITE_ONCE(p->resetting,false);
			spin_unlock_irqrestore(&p->lock,flags);
		} else if(value!=USB_PORT_FEAT_POWER) ret=-EPIPE;
		break;
	case ClearPortFeature:
		if(index!=1) { ret=-EPIPE; break; }
		spin_lock_irqsave(&p->lock,flags);
		if(value>=USB_PORT_FEAT_C_CONNECTION && value<=USB_PORT_FEAT_C_RESET)
			p->change&=~BIT(value-USB_PORT_FEAT_C_CONNECTION);
		else if(value==USB_PORT_FEAT_ENABLE) p->enabled=false;
		else if(value!=USB_PORT_FEAT_POWER) ret=-EPIPE;
		spin_unlock_irqrestore(&p->lock,flags);
		break;
	case SetHubFeature: case ClearHubFeature: break;
	default: ret=-EPIPE;
	}
	mutex_unlock(&p->io); return ret;
}
static int mini_frame(struct usb_hcd *hcd) { return jiffies_to_msecs(jiffies)&0x7ff; }
static const struct hc_driver mini_hc_driver={
	.description="mini-usb",.product_desc="riscv-mini full-speed PIO host",.hcd_priv_size=sizeof(struct mini_usb),
	.flags=HCD_USB11|HCD_MEMORY,.start=mini_usb_start,.stop=mini_usb_stop,
	.urb_enqueue=mini_enqueue,.urb_dequeue=mini_dequeue,.get_frame_number=mini_frame,
	.hub_status_data=mini_hub_status,.hub_control=mini_hub_control,
};
static int mini_usb_probe(struct platform_device *pdev)
{
	struct usb_hcd *hcd; struct mini_usb *p; struct resource *res;
	u32 ready; int ret;
	if(usb_disabled()) return -ENODEV;
	hcd=usb_create_hcd(&mini_hc_driver,&pdev->dev,dev_name(&pdev->dev)); if(!hcd) return -ENOMEM;
	p=mini_hcd(hcd); p->hcd=hcd;
	p->regs=devm_platform_get_and_ioremap_resource(pdev,0,&res);
	p->ctrl=devm_platform_ioremap_resource(pdev,1);
	p->phyreset=devm_platform_ioremap_resource(pdev,2);
	if(IS_ERR(p->regs)||IS_ERR(p->ctrl)||IS_ERR(p->phyreset)) { ret=-ENODEV; goto put; }
	/* Stop serial mode and observe ready=0 before resetting the shared PHY.
	 * This avoids accepting a stale ready/id snapshot while its PLL restarts.
	 * Ethernet reapplies RMII settings on ndo_open; runtime USB resets use SE0. */
	writel(1,p->ctrl+4); writel(0,p->ctrl);
	ret=readl_poll_timeout(p->ctrl+8,ready,!ready,10,200000); if(ret) goto put;
	writel(0,p->phyreset); msleep(20); writel(1,p->phyreset); msleep(50);
	writel(1,p->ctrl); writel(1,p->ctrl+4);
	ret=readl_poll_timeout(p->ctrl+8,ready,ready,10,200000); if(ret) goto put;
	if(readl(p->ctrl+16)!=0x00060424 || readl(p->ctrl+12)) { ret=-ENODEV; goto put; }
	writel(0,p->ctrl+4); msleep(2); writel(0,p->regs+IRQ_MASK); writel(15,p->regs+IRQ_ACK);
	writel(RESET,p->regs+CTRL); msleep(100); writel(RUN,p->regs+CTRL);
	spin_lock_init(&p->lock); mutex_init(&p->io); INIT_LIST_HEAD(&p->queue);
	INIT_DELAYED_WORK(&p->work,mini_usb_work);
	hcd->rsrc_start=res->start; hcd->rsrc_len=resource_size(res);
	ret=usb_add_hcd(hcd,0,0); if(ret) goto put;
	platform_set_drvdata(pdev,hcd); return 0;
put:
	usb_put_hcd(hcd); return dev_err_probe(&pdev->dev,ret,"USB host initialization failed\n");
}
static void mini_usb_shutdown(struct platform_device *pdev) { usb_remove_hcd(platform_get_drvdata(pdev)); }
static const struct of_device_id mini_usb_match[]={ {.compatible="riscv-mini,liteusb"},{} };
static struct platform_driver mini_usb_driver={.probe=mini_usb_probe,.shutdown=mini_usb_shutdown,
	.driver={.name="mini-usb",.of_match_table=mini_usb_match,.suppress_bind_attrs=true}};
builtin_platform_driver(mini_usb_driver);
