#include "ethernet_dma.h"
#include "ethernet_phy.h"
#include "ae350_address_map.h"
#include "ae350_dma.h"
#define W(a) (*(volatile uint32_t *)(uintptr_t)(a))
static struct ethernet_ring_config ring;
static uint32_t txprod,rxcons;
static unsigned tx_lease,rx_lease,initialized;
uint32_t ethernet_packet_reg(unsigned off) {return W(AE350_ETH_BASE+off);}
static void reg(unsigned off,uint32_t value) {W(AE350_ETH_BASE+off)=value;}
static void publish(void) {__asm__ volatile("fence iorw, iorw" ::: "memory");}
static int range(uintptr_t address,unsigned bytes) {
    return address>=0x100000u && !(address&31u) && bytes &&
        address<AE350_DDR_SIZE && bytes<=AE350_DDR_SIZE-address;
}
static int overlap(uintptr_t a,unsigned an,uintptr_t b,unsigned bn) {return a<b+bn&&b<a+an;}
static int clean(const void *p,unsigned n) {return ae350_dma_sync_for_device((uint32_t)(uintptr_t)p,n);}
static int invalidate(const void *p,unsigned n) {return ae350_dma_sync_for_cpu((uint32_t)(uintptr_t)p,n);}
/* Force a read response through the CPU AHB adapter after its last line
 * writeback. The adapter flushes its posted write line before a NONSEQ read;
 * a CPU fence alone does not describe that separate fabric buffer. */
static int descriptor_barrier(struct ethernet_descriptor *d) {
    if(invalidate(d,32))return -1;
    uint32_t flags=((volatile struct ethernet_descriptor *)d)->flags;
    publish();return (flags&ETH_DMA_OWN)?0:-1;
}
int ethernet_dma_stop(void) {
    initialized=0;reg(0x80,0);publish();
    uint64_t start=ethernet_cycles();
    while(ethernet_packet_reg(0x84)&3u) {
        if(ethernet_cycles()-start>AE350_CPU_HZ/10u)return -1;
    }
    initialized=0;tx_lease=rx_lease=0;return 0;
}
int ethernet_dma_init(const struct ethernet_ring_config *c) {
    if(!c||c->count<2||c->count>256||(c->count&(c->count-1))||c->stride<1536||c->stride>65536||(c->stride&31))return -1;
    uintptr_t addresses[4]={(uintptr_t)c->tx,(uintptr_t)c->rx,(uintptr_t)c->tx_buffers,(uintptr_t)c->rx_buffers};
    unsigned sizes[4]={c->count*32,c->count*32,c->count*c->stride,c->count*c->stride};
    for(unsigned i=0;i<4;i++) {
        if(!range(addresses[i],sizes[i]))return -1;
        for(unsigned j=0;j<i;j++)if(overlap(addresses[i],sizes[i],addresses[j],sizes[j]))return -1;
    }
    if(ethernet_packet_reg(0x10)!=0xae350e03u||ethernet_dma_stop())return -2;
    ring=*c;txprod=rxcons=0;
    reg(0x80,2);reg(0xa8,0);reg(0xa4,7);
    for(unsigned i=0;i<c->count;i++) {
        struct ethernet_descriptor *t=&c->tx[i],*r=&c->rx[i];
        for(unsigned j=0;j<8;j++) {((uint32_t *)t)[j]=0;((uint32_t *)r)[j]=0;}
        t->buffer=(uint32_t)(uintptr_t)(c->tx_buffers+i*c->stride);t->capacity=1536;t->cookie=i;
        r->buffer=(uint32_t)(uintptr_t)(c->rx_buffers+i*c->stride);r->capacity=1536;r->flags=ETH_DMA_OWN;r->cookie=i;
        for(unsigned j=0;j<1536;j++) {c->tx_buffers[i*c->stride+j]=0xa5;c->rx_buffers[i*c->stride+j]=0xa5;}
    }
    if(clean(c->tx_buffers,sizes[2])||clean(c->rx_buffers,sizes[3])||clean(c->tx,sizes[0])||clean(c->rx,sizes[1]))return -3;
    if(descriptor_barrier(&c->rx[c->count-1]))return -3;
    reg(0x88,(uint32_t)(uintptr_t)c->tx);reg(0x8c,(uint32_t)(uintptr_t)c->rx);reg(0x90,c->count-1);
    publish();reg(0x80,1);initialized=1;return 0;
}
int ethernet_dma_tx_acquire(uint8_t **buffer) {
    if(!initialized||!buffer||tx_lease||ethernet_packet_reg(0xac))return -1;
    uint32_t done=ethernet_packet_reg(0x98);
    if(txprod-done>=ring.count)return 0;
    struct ethernet_descriptor *d=&ring.tx[txprod&(ring.count-1)];
    if(invalidate(d,32)||d->flags&(ETH_DMA_OWN|ETH_DMA_ERROR))return -2;
    *buffer=ring.tx_buffers+(txprod&(ring.count-1))*ring.stride;tx_lease=1;return 1;
}
int ethernet_dma_tx_submit(unsigned n) {
    if(!initialized||!tx_lease||n<14||n>1530)return -1;
    unsigned slot=txprod&(ring.count-1);struct ethernet_descriptor *d=&ring.tx[slot];
    d->length=n;d->flags=ETH_DMA_OWN;
    if(clean(ring.tx_buffers+slot*ring.stride,1536)||clean(d,32))return -2;
    if(descriptor_barrier(d))return -2;
    publish();reg(0x94,++txprod);tx_lease=0;return 0;
}
void ethernet_dma_tx_cancel(void) {tx_lease=0;}
int ethernet_dma_rx_acquire(const uint8_t **buffer,unsigned *bytes) {
    if(!initialized||!buffer||!bytes||rx_lease||ethernet_packet_reg(0xac))return -1;
    if(rxcons==ethernet_packet_reg(0x9c))return 0;
    struct ethernet_descriptor *d=&ring.rx[rxcons&(ring.count-1)];
    if(invalidate(d,32))return -2;
    if((d->flags&(ETH_DMA_OWN|ETH_DMA_DONE|ETH_DMA_ERROR))!=ETH_DMA_DONE||!d->length||d->length>1530)return -3;
    const uint8_t *p=ring.rx_buffers+(rxcons&(ring.count-1))*ring.stride;
    if(invalidate(p,1536))return -2;
    for(unsigned i=1530;i<1536;i++)if(p[i]!=0xa5)return -4;
    *buffer=p;*bytes=d->length;rx_lease=1;return 1;
}
int ethernet_dma_rx_release(void) {
    if(!initialized||!rx_lease)return -1;
    unsigned slot=rxcons&(ring.count-1);struct ethernet_descriptor *d=&ring.rx[slot];
    d->length=0;d->flags=ETH_DMA_OWN;
    if(clean(ring.rx_buffers+slot*ring.stride,1536)||clean(d,32))return -2;
    if(descriptor_barrier(d))return -2;
    publish();reg(0xa0,++rxcons);rx_lease=0;return 0;
}
