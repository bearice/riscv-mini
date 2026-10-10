// SPDX-License-Identifier: GPL-2.0
/* Exclusive LiteEth DDR descriptor-ring backend; no packet SRAM fallback. */
#include <linux/delay.h>
#include <linux/dma-mapping.h>
#include <linux/etherdevice.h>
#include <linux/interrupt.h>
#include <linux/iopoll.h>
#include <linux/of.h>
#include <linux/of_net.h>
#include <linux/phy.h>
#include <linux/rtnetlink.h>
#include <linux/platform_device.h>

#define COUNT 4
#define BUFFER 1536
#define OWN BIT(31)
#define DONE BIT(30)
#define ERROR BIT(29)
enum { CONTROL, RX_BASE, TX_BASE, MASK, RX_CONS, TX_PROD, RX_PROD,
	TX_CONS, BUSY, ERR, RX_PACKETS, TX_PACKETS, PENDING, ENABLE, REGS };
struct mini_desc { u32 buffer, capacity, status, cookie; };
struct mini_net {
	struct net_device *ndev;
	void __iomem *regs, *phy;
	u32 offsets[REGS];
	void *memory;
	dma_addr_t dma;
	struct mini_desc *rx, *tx;
	u8 *rxbuf, *txbuf;
	u16 rx_cons, tx_prod, tx_cons;
	struct napi_struct napi;
	struct mii_bus *mdio;
	struct phy_device *phydev;
	spinlock_t lock;
	int irq;
};
#define MEMORY_BYTES (COUNT * (2 * sizeof(struct mini_desc) + 2 * BUFFER))
static u32 nr(struct mini_net *p, unsigned int r) { return readl(p->regs+p->offsets[r]); }
static void nw(struct mini_net *p, unsigned int r, u32 v) { writel(v,p->regs+p->offsets[r]); }
static void invalidate(void) { asm volatile("fence rw,rw\n.word 0x0000500f" ::: "memory"); }
static int mdio_bit(struct mini_net *p, bool output, bool value)
{
	u32 pins = (output ? 2 : 0) | (value ? 4 : 0);
	int received;
	writel(pins, p->phy+4); udelay(1);
	received = readl(p->phy+8) & 1;
	writel(pins|1, p->phy+4); udelay(1); writel(pins, p->phy+4);
	return received;
}
static void mdio_bits(struct mini_net *p, u32 value, int count)
{ while(count) mdio_bit(p,true,(value >> --count)&1); }
static void mdio_header(struct mini_net *p,int addr,int reg,bool read)
{ mdio_bits(p,~0u,32); mdio_bits(p,1,2); mdio_bits(p,read?2:1,2); mdio_bits(p,addr,5); mdio_bits(p,reg,5); }
static int mini_mdio_read(struct mii_bus *bus, int addr, int reg)
{
	struct mini_net *p=bus->priv;
	int i, ack; u16 value=0;
	mdio_header(p,addr,reg,true); mdio_bit(p,false,1); ack=mdio_bit(p,false,1);
	for(i=0;i<16;i++) value=(value<<1)|mdio_bit(p,false,1);
	mdio_bit(p,false,1); writel(0,p->phy+4); return ack?-EIO:value;
}
static int mini_mdio_write(struct mii_bus *bus,int addr,int reg,u16 value)
{
	struct mini_net *p=bus->priv;
	mdio_header(p,addr,reg,false); mdio_bits(p,2,2); mdio_bits(p,value,16);
	mdio_bit(p,false,1); writel(0,p->phy+4); return 0;
}
static void mini_adjust_link(struct net_device *ndev)
{
	struct mini_net *p=netdev_priv(ndev);
	if(p->phydev->link) netif_carrier_on(ndev); else netif_carrier_off(ndev);
	phy_print_status(p->phydev);
}
static int mini_poll(struct napi_struct *napi,int budget)
{
	struct mini_net *p=container_of(napi,struct mini_net,napi);
	struct net_device *ndev=p->ndev;
	int work=0;
	unsigned long flags;
	invalidate();
	spin_lock_irqsave(&p->lock,flags);
	while(p->tx_cons != (u16)nr(p,TX_CONS)) {
		unsigned int slot=p->tx_cons++ & (COUNT-1);
		if(READ_ONCE(p->tx[slot].status)&ERROR) ndev->stats.tx_errors++;
		else { ndev->stats.tx_packets++; ndev->stats.tx_bytes+=p->tx[slot].capacity; }
	}
	if(netif_queue_stopped(ndev) && (u16)(p->tx_prod-p->tx_cons)<COUNT)
		netif_wake_queue(ndev);
	spin_unlock_irqrestore(&p->lock,flags);
	while(work<budget && p->rx_cons!=(u16)nr(p,RX_PROD)) {
		unsigned int slot=p->rx_cons & (COUNT-1);
		u32 status=READ_ONCE(p->rx[slot].status);
		unsigned int len=status&0xfff;
		struct sk_buff *skb;
		if((status&OWN) || !(status&DONE)) break;
		if((status&ERROR) || len<14 || len>1518) ndev->stats.rx_errors++;
		else {
			skb=napi_alloc_skb(napi,len+NET_IP_ALIGN);
			if(!skb) ndev->stats.rx_dropped++;
			else {
				skb_reserve(skb,NET_IP_ALIGN);
				skb_put_data(skb,p->rxbuf+slot*BUFFER,len);
				skb->protocol=eth_type_trans(skb,ndev);
				ndev->stats.rx_packets++; ndev->stats.rx_bytes+=len;
				napi_gro_receive(napi,skb);
			}
		}
		WRITE_ONCE(p->rx[slot].status,OWN); dma_wmb(); nw(p,RX_CONS,++p->rx_cons); work++;
		invalidate();
	}
	if(work<budget && napi_complete_done(napi,work)) {
		nw(p,PENDING,3); nw(p,ENABLE,3);
		/* Close the acknowledgement/re-enable race using producer counters. */
		if(p->rx_cons!=(u16)nr(p,RX_PROD) || p->tx_cons!=(u16)nr(p,TX_CONS)) {
			nw(p,ENABLE,0); napi_schedule(napi);
		}
	}
	return work;
}
static irqreturn_t mini_net_irq(int irq,void *data)
{
	struct mini_net *p=data;
	if(!nr(p,PENDING)) return IRQ_NONE;
	nw(p,ENABLE,0); napi_schedule_irqoff(&p->napi); return IRQ_HANDLED;
}
static int mini_ring_stop(struct mini_net *p)
{
	u32 busy;
	nw(p,ENABLE,0); nw(p,CONTROL,0);
	return read_poll_timeout(nr,busy,!busy,10,100000,false,p,BUSY);
}
static int mini_open(struct net_device *ndev)
{
	struct mini_net *p=netdev_priv(ndev);
	int i,value,ret=mini_ring_stop(p);
	if(ret) return ret;
	/* USB initial PHY reset shares F10; restore board-specific RMII settings
	 * when the network is brought up, after all initial peripheral probes. */
	phy_write(p->phydev,31,7); value=phy_read(p->phydev,16);
	if(value>=0) phy_write(p->phydev,16,(value|8)&~0x1006);
	phy_write(p->phydev,24,1); phy_write(p->phydev,31,0);
	value=phy_read(p->phydev,24); if(value>=0) phy_write(p->phydev,24,value&~0x8000);
	phy_write_mmd(p->phydev,7,60,0);
	p->rx_cons=p->tx_prod=p->tx_cons=0;
	for(i=0;i<COUNT;i++) {
		p->rx[i]=(struct mini_desc){p->dma+2*COUNT*sizeof(struct mini_desc)+i*BUFFER,BUFFER,OWN,i};
		p->tx[i]=(struct mini_desc){p->dma+2*COUNT*sizeof(struct mini_desc)+COUNT*BUFFER+i*BUFFER,0,0,i};
	}
	dma_wmb(); nw(p,RX_BASE,p->dma); nw(p,TX_BASE,p->dma+COUNT*sizeof(struct mini_desc));
	nw(p,MASK,COUNT-1); nw(p,RX_CONS,0); nw(p,TX_PROD,0); nw(p,PENDING,3);
	ret=request_irq(p->irq,mini_net_irq,0,ndev->name,p); if(ret) return ret;
	napi_enable(&p->napi); nw(p,CONTROL,1); nw(p,ENABLE,3);
	phy_start(p->phydev); netif_start_queue(ndev); return 0;
}
static int mini_close(struct net_device *ndev)
{
	struct mini_net *p=netdev_priv(ndev);
	netif_stop_queue(ndev); phy_stop(p->phydev);
	nw(p,ENABLE,0); synchronize_irq(p->irq); napi_disable(&p->napi);
	if(mini_ring_stop(p)) netdev_err(ndev,"DMA stop timed out; ring retained\n");
	free_irq(p->irq,p); return 0;
}
static netdev_tx_t mini_xmit(struct sk_buff *skb,struct net_device *ndev)
{
	struct mini_net *p=netdev_priv(ndev);
	unsigned long flags; unsigned int slot,len;
	if(skb->len>1518) { ndev->stats.tx_dropped++; dev_kfree_skb(skb); return NETDEV_TX_OK; }
	spin_lock_irqsave(&p->lock,flags);
	if((u16)(p->tx_prod-p->tx_cons)>=COUNT) {
		netif_stop_queue(ndev); spin_unlock_irqrestore(&p->lock,flags); return NETDEV_TX_BUSY;
	}
	slot=p->tx_prod&(COUNT-1); len=max(skb->len,60u);
	memset(p->txbuf+slot*BUFFER,0,len);
	if(skb_copy_bits(skb,0,p->txbuf+slot*BUFFER,skb->len)) {
		ndev->stats.tx_dropped++; spin_unlock_irqrestore(&p->lock,flags);
		dev_kfree_skb(skb); return NETDEV_TX_OK;
	}
	p->tx[slot].capacity=len; WRITE_ONCE(p->tx[slot].status,OWN);
	dma_wmb(); nw(p,TX_PROD,++p->tx_prod);
	if((u16)(p->tx_prod-p->tx_cons)>=COUNT) netif_stop_queue(ndev);
	spin_unlock_irqrestore(&p->lock,flags); dev_kfree_skb(skb); return NETDEV_TX_OK;
}
static const struct net_device_ops mini_net_ops={
	.ndo_open=mini_open,.ndo_stop=mini_close,.ndo_start_xmit=mini_xmit,
	.ndo_set_mac_address=eth_mac_addr,.ndo_validate_addr=eth_validate_addr,
};
static int mini_net_probe(struct platform_device *pdev)
{
	struct net_device *ndev=alloc_etherdev(sizeof(struct mini_net));
	struct mini_net *p; struct resource *res; int ret,i,value;
	if(!ndev) return -ENOMEM;
	SET_NETDEV_DEV(ndev,&pdev->dev); p=netdev_priv(ndev); p->ndev=ndev;
	spin_lock_init(&p->lock);
	p->regs=devm_platform_get_and_ioremap_resource(pdev,0,&res);
	p->phy=devm_platform_ioremap_resource(pdev,1);
	if(IS_ERR(p->regs)||IS_ERR(p->phy)) { ret=-ENODEV; goto free; }
	ret=of_property_read_u32_array(pdev->dev.of_node,"riscv-mini,csr-offsets",p->offsets,REGS);
	if(ret) goto free;
	for(i=0;i<REGS;i++) if((p->offsets[i]&3)||p->offsets[i]+4>resource_size(res)) { ret=-EINVAL; goto free; }
	p->irq=platform_get_irq(pdev,0); if(p->irq<0) { ret=p->irq; goto free; }
	ret=mini_ring_stop(p); if(ret) goto free;
	ret=dma_set_mask_and_coherent(&pdev->dev,DMA_BIT_MASK(32)); if(ret) goto free;
	p->memory=dma_alloc_coherent(&pdev->dev,MEMORY_BYTES,&p->dma,GFP_KERNEL);
	if(!p->memory) { ret=-ENOMEM; goto free; }
	if(!p->dma || p->dma+MEMORY_BYTES>0x07e00000) { ret=-ERANGE; goto dma; }
	p->rx=p->memory; p->tx=p->rx+COUNT; p->rxbuf=(u8 *)(p->tx+COUNT); p->txbuf=p->rxbuf+COUNT*BUFFER;
	writel(1,p->phy); udelay(10); writel(0,p->phy); msleep(10);
	p->mdio=mdiobus_alloc(); if(!p->mdio) { ret=-ENOMEM; goto dma; }
	p->mdio->name="mini-mdio"; p->mdio->read=mini_mdio_read; p->mdio->write=mini_mdio_write;
	p->mdio->priv=p; p->mdio->parent=&pdev->dev;
	snprintf(p->mdio->id,MII_BUS_ID_SIZE,"%s",dev_name(&pdev->dev));
	/* Only instantiate the first responding PHY, as the qualified firmware
	 * does. Probing address aliases would reset the same physical PHY many times. */
	for(i=1;i<32;i++) {
		int id1=mini_mdio_read(p->mdio,i,2),id2=mini_mdio_read(p->mdio,i,3);
		if(id1>0 && id1!=0xffff && id2>=0 && id2!=0xffff) break;
	}
	if(i==32) { ret=-ENODEV; goto bus; }
	p->mdio->phy_mask=~BIT(i);
	dev_info(&pdev->dev,"MDIO PHY address %d\n",i);
	ret=mdiobus_register(p->mdio); if(ret) goto bus;
	p->phydev=phy_find_first(p->mdio); if(!p->phydev) { ret=-ENODEV; goto unregister_bus; }
	ret=phy_connect_direct(ndev,p->phydev,mini_adjust_link,PHY_INTERFACE_MODE_RMII);
	if(ret) goto unregister_bus;
	/* Preserve board-specific RTL8201F RMII REF_CLK timing and disable EEE. */
	phy_write(p->phydev,31,7); value=phy_read(p->phydev,16);
	if(value>=0) phy_write(p->phydev,16,(value|8)&~0x1006);
	phy_write(p->phydev,24,1); phy_write(p->phydev,31,0);
	value=phy_read(p->phydev,24); if(value>=0) phy_write(p->phydev,24,value&~0x8000);
	phy_write_mmd(p->phydev,7,60,0);
	linkmode_zero(p->phydev->supported);
	linkmode_set_bit(ETHTOOL_LINK_MODE_100baseT_Full_BIT,p->phydev->supported);
	linkmode_set_bit(ETHTOOL_LINK_MODE_Autoneg_BIT,p->phydev->supported);
	linkmode_set_bit(ETHTOOL_LINK_MODE_TP_BIT,p->phydev->supported);
	linkmode_copy(p->phydev->advertising,p->phydev->supported);
	ret=of_get_ethdev_address(pdev->dev.of_node,ndev);
	if(ret) eth_hw_addr_random(ndev);
	ndev->netdev_ops=&mini_net_ops; ndev->min_mtu=68; ndev->max_mtu=1500;
	netif_carrier_off(ndev); netif_napi_add(ndev,&p->napi,mini_poll);
	ret=register_netdev(ndev); if(ret) goto disconnect;
	platform_set_drvdata(pdev,ndev); return 0;
disconnect:
	netif_napi_del(&p->napi); phy_disconnect(p->phydev);
unregister_bus:
	mdiobus_unregister(p->mdio);
bus:
	mdiobus_free(p->mdio);
dma:
	dma_free_coherent(&pdev->dev,MEMORY_BYTES,p->memory,p->dma);
free:
	free_netdev(ndev); return dev_err_probe(&pdev->dev,ret,"Ethernet initialization failed\n");
}
static void mini_net_shutdown(struct platform_device *pdev)
{
	struct net_device *ndev=platform_get_drvdata(pdev);
	rtnl_lock(); if(netif_running(ndev)) dev_close(ndev); rtnl_unlock();
}
static const struct of_device_id mini_net_match[]={ {.compatible="riscv-mini,liteeth-ring"},{} };
static struct platform_driver mini_net_driver={ .probe=mini_net_probe,.shutdown=mini_net_shutdown,
	.driver={.name="mini-eth",.of_match_table=mini_net_match,.suppress_bind_attrs=true} };
builtin_platform_driver(mini_net_driver);
