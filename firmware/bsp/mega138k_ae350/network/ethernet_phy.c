#include "ethernet_phy.h"
#include "ae350_address_map.h"
#define W(a) (*(volatile uint32_t *)(uintptr_t)(a))
uint64_t ethernet_cycles(void) {
    uint32_t hi,lo,again;
    do {__asm__ volatile("rdcycleh %0":"=r"(hi));__asm__ volatile("rdcycle %0":"=r"(lo));__asm__ volatile("rdcycleh %0":"=r"(again));}while(hi!=again);
    return ((uint64_t)hi<<32)|lo;
}
void ethernet_delay_us(unsigned us) {uint64_t t=ethernet_cycles();while(ethernet_cycles()-t<(uint64_t)us*(AE350_CPU_HZ/1000000u)) {}}
static unsigned bit(unsigned value) {
    unsigned ctrl=1u|(value?0u:4u);W(AE350_ETH_BASE+4)=ctrl;ethernet_delay_us(1);
    W(AE350_ETH_BASE+4)=ctrl|2u;ethernet_delay_us(1);
    unsigned v=W(AE350_ETH_BASE+8)&1u;W(AE350_ETH_BASE+4)=ctrl;return v;
}
static void send(uint32_t value,unsigned bits) {while(bits--)bit((value>>bits)&1u);}
int ethernet_mdio_read(unsigned phy,unsigned reg,uint16_t *value) {
    if(phy>31||reg>31||!value)return -1;
    send(~0u,32);send((6u<<10)|(phy<<5)|reg,14);
    unsigned ack=bit(1),v=0;for(unsigned i=0;i<16;i++)v=(v<<1)|bit(1);bit(1);
    *value=(uint16_t)v;return ack?-1:0;
}
int ethernet_mdio_write(unsigned phy,unsigned reg,uint16_t value) {
    if(phy>31||reg>31)return -1;
    send(~0u,32);send((5u<<28)|(phy<<23)|(reg<<18)|(2u<<16)|value,32);bit(1);return 0;
}
int ethernet_phy_init(unsigned *address,uint32_t *identity) {
    if(!address||!identity||W(AE350_ETH_BASE)!=0xae350e01u||W(AE350_ETH_BASE+12)!=25000000u)return -1;
    W(AE350_ETH_BASE+4)=0;ethernet_delay_us(20000);W(AE350_ETH_BASE+4)=1;ethernet_delay_us(100000);
    for(unsigned index=1;index<=32;index++) {
        unsigned p=index&31u;
        uint16_t a,b;
        if(!ethernet_mdio_read(p,2,&a)&&!ethernet_mdio_read(p,3,&b)) {
            uint32_t id=((uint32_t)a<<16)|b;
            if(id==0x001cc916u) {*address=p;*identity=id;return 0;}
        }
    }
    return -2;
}
#ifndef AE350_ETH_PHY_RX_DELAY
#define AE350_ETH_PHY_RX_DELAY 1
#endif
#if AE350_ETH_PHY_RX_DELAY != 0 && AE350_ETH_PHY_RX_DELAY != 1
#error "AE350_ETH_PHY_RX_DELAY must be 0 or 1"
#endif
static int phy_delays(unsigned p,unsigned program) {
    uint16_t page,selected,tx,rx;
    if(ethernet_mdio_read(p,31,&page)||page==0xffffu)return -1;
    ethernet_mdio_write(p,31,0xd08);
    int error=ethernet_mdio_read(p,31,&selected);
    if(error||selected!=0xd08u) {ethernet_mdio_write(p,31,page);return -6;}
    error=ethernet_mdio_read(p,0x11,&tx)|ethernet_mdio_read(p,0x15,&rx);
    if(!error&&(tx==0xffffu||rx==0xffffu))error=-1;
    if(!error&&program) {
        ethernet_mdio_write(p,0x11,tx|0x100u);ethernet_mdio_write(p,0x15,(rx&~8u)|(AE350_ETH_PHY_RX_DELAY?8u:0u));
        error=ethernet_mdio_read(p,0x11,&tx)|ethernet_mdio_read(p,0x15,&rx);
    }
    ethernet_mdio_write(p,31,page);
    if(error)return -1;
    return (tx&0x100u)&&!!(rx&8u)==AE350_ETH_PHY_RX_DELAY?0:-3;
}
int ethernet_phy_gigabit(unsigned p) {
    uint16_t v,id1,id2;
    if(ethernet_mdio_read(p,2,&id1)||ethernet_mdio_read(p,3,&id2)||
       (((uint32_t)id1<<16)|id2)!=0x001cc916u)return -5;
    /* Linux realtek.c: page d08/reg11 bit8 TX, reg15 bit3 RX, each2ns.
       Preserve PAGSR rather than assuming the standard page is zero.
       https://kernel.googlesource.com/pub/scm/linux/kernel/git/torvalds/linux/+/d4c08d8b23b22807c712208cd05cb047e92e7672/drivers/net/phy/realtek.c */
    int error=phy_delays(p,1);if(error)return error;
    ethernet_mdio_write(p,0,0x8000);
    unsigned n;
    for(n=0;n<500;n++) {if(ethernet_mdio_read(p,0,&v))return -1;if(!(v&0x8000))break;ethernet_delay_us(1000);}
    if(n==500)return -2;
    error=phy_delays(p,0);if(error)return error;
    if(ethernet_mdio_read(p,4,&v))return -1;
    ethernet_mdio_write(p,4,(v&~0xde0u)|1u);
    if(ethernet_mdio_read(p,4,&v)||(v&0xde0u)||(v&0x1fu)!=1u)return -7;
    if(ethernet_mdio_read(p,9,&v))return -1;
    ethernet_mdio_write(p,9,(v&~0x300u)|0x200u);
    if(ethernet_mdio_read(p,9,&v)||(v&0x300u)!=0x200u)return -7;
    ethernet_mdio_write(p,0,0x1200);
    for(n=0;n<100;n++) {
        ethernet_mdio_read(p,1,&v);
        if(!ethernet_mdio_read(p,1,&v)&&(v&0x24)==0x24)return 0;
        ethernet_delay_us(100000);
    }
    return -4;
}
