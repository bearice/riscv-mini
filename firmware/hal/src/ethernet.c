/* RTL8201F clause-22 MDIO + LiteEth packet slots. All packet work is deferred. */
#include <hal/hal.h>
#include <hal/dma.h>
#include <string.h>
#include <generated/csr.h>
#include <generated/soc.h>
#include <generated/mem.h>
static hal_eth_info_t info;
static unsigned next_poll;
#if MINI_FEATURE_ETH_DMA != defined(CONFIG_ETH_RING_DMA)
#error "Ethernet DMA builds require the ring backend"
#endif
#ifndef CONFIG_ETH_RING_DMA
static volatile unsigned tx_busy;
static unsigned tx_slot;
#endif
#ifdef CONFIG_ETH_RING_DMA
#define RING_COUNT 4u
#define RING_OWN (1u<<31)
#define RING_DONE (1u<<30)
#define RING_ERROR (1u<<29)
typedef struct {uint32_t buffer,capacity,status,cookie;} ring_descriptor_t;
static _Alignas(16) volatile ring_descriptor_t rx_ring[RING_COUNT],tx_ring[RING_COUNT];
static _Alignas(16) uint8_t rx_pool[RING_COUNT][1536],tx_pool[RING_COUNT][1536];
static uint16_t rx_consumer,tx_producer;
static unsigned rx_borrowed,tx_borrowed;
static void ring_irq(void *unused) {
    (void)unused;++info.irqs;
    /* The RX level remains asserted until the consumer releases the frame.
       Disable its notification while the main loop owns the queued buffers. */
    unsigned pending=eth_dma_ev_pending_read();
    eth_dma_ev_pending_write(2);if(pending&1u)eth_dma_ev_enable_write(2);
}
static void ring_init(void) {
    rx_consumer=tx_producer=0;rx_borrowed=tx_borrowed=0;
    for(unsigned i=0;i<RING_COUNT;++i) {
        rx_ring[i]=(ring_descriptor_t){(uintptr_t)rx_pool[i],1536,RING_OWN,i};
        tx_ring[i]=(ring_descriptor_t){(uintptr_t)tx_pool[i],0,0,i};
    }
    __asm__ volatile("fence rw,rw":::"memory");
    eth_dma_rx_base_write((uintptr_t)rx_ring);eth_dma_tx_base_write((uintptr_t)tx_ring);
    eth_dma_mask_write(RING_COUNT-1);eth_dma_rx_consumer_write(0);eth_dma_tx_producer_write(0);
    eth_dma_ev_pending_write(3);eth_dma_ev_enable_write(3);
    hal_irq_attach(ETH_DMA_INTERRUPT,ring_irq,0);hal_irq_enable(ETH_DMA_INTERRUPT,1);
    eth_dma_control_write(1);
}
hal_result_t hal_eth_rx_acquire(const void **frame,unsigned *length) {
    if(!frame || !length)return HAL_INVALID;
    *frame=0;*length=0;
    if(!info.initialized)return HAL_NO_MEDIA;
    if(rx_borrowed)return HAL_BUSY;
    if((uint16_t)eth_dma_rx_producer_read()==rx_consumer)return HAL_BUSY;
    hal_dma_invalidate();
    volatile ring_descriptor_t *d=&rx_ring[rx_consumer&(RING_COUNT-1)];
    unsigned status=d->status;
    if((status&RING_OWN) || !(status&RING_DONE))return HAL_IO;
    rx_borrowed=1;*frame=rx_pool[rx_consumer&(RING_COUNT-1)];*length=status&0xfffu;
    if((status&RING_ERROR) || *length<14 || *length>HAL_ETH_MAX_FRAME) {
        hal_eth_rx_release(*frame);*frame=0;*length=0;return HAL_INVALID;
    }
    ++info.rx_frames;return HAL_OK;
}
hal_result_t hal_eth_rx_release(const void *frame) {
    if(!rx_borrowed || frame!=rx_pool[rx_consumer&(RING_COUNT-1)])return HAL_INVALID;
    rx_ring[rx_consumer&(RING_COUNT-1)].status=RING_OWN;
    __asm__ volatile("fence rw,rw":::"memory");
    rx_consumer++;rx_borrowed=0;eth_dma_rx_consumer_write(rx_consumer);
    eth_dma_ev_enable_write(3);return HAL_OK;
}
hal_result_t hal_eth_tx_acquire(void **frame,unsigned *capacity) {
    if(!frame || !capacity)return HAL_INVALID;
    *frame=0;*capacity=0;
    if(!info.initialized || !info.link)return HAL_NO_MEDIA;
    if(tx_borrowed || (uint16_t)(tx_producer-(uint16_t)eth_dma_tx_consumer_read())>=RING_COUNT)return HAL_BUSY;
    tx_borrowed=1;*frame=tx_pool[tx_producer&(RING_COUNT-1)];*capacity=HAL_ETH_MAX_FRAME;return HAL_OK;
}
hal_result_t hal_eth_tx_commit(const void *frame,unsigned length) {
    if(!tx_borrowed || frame!=tx_pool[tx_producer&(RING_COUNT-1)])return HAL_INVALID;
    if(length<14 || length>HAL_ETH_MAX_FRAME) {tx_borrowed=0;return HAL_INVALID;}
    volatile ring_descriptor_t *d=&tx_ring[tx_producer&(RING_COUNT-1)];
    d->capacity=length;d->status=RING_OWN;
    __asm__ volatile("fence rw,rw":::"memory");
    tx_producer++;tx_borrowed=0;eth_dma_tx_producer_write(tx_producer);++info.tx_frames;return HAL_OK;
}
#else
hal_result_t hal_eth_rx_acquire(const void **frame,unsigned *length) {(void)frame;(void)length;return HAL_UNSUPPORTED;}
hal_result_t hal_eth_rx_release(const void *frame) {(void)frame;return HAL_UNSUPPORTED;}
hal_result_t hal_eth_tx_acquire(void **frame,unsigned *capacity) {(void)frame;(void)capacity;return HAL_UNSUPPORTED;}
hal_result_t hal_eth_tx_commit(const void *frame,unsigned length) {(void)frame;(void)length;return HAL_UNSUPPORTED;}
#endif
static void half_cycle(void) {
    unsigned start=hal_ticks();while((unsigned)(hal_ticks()-start)<CONFIG_CLOCK_FREQUENCY/1000000u) {}
}
static unsigned bit(unsigned output,unsigned value) {
    unsigned pins=(output?2u:0u)|(value?4u:0u);
    ethphy_mdio_w_write(pins);half_cycle();
    /* Sample the bit established during MDC low. RTL8201F advances MDIO
       after the rising edge; sampling after that edge reads the NEXT bit. */
    unsigned received=ethphy_mdio_r_read()&1u;
    ethphy_mdio_w_write(pins|1u);half_cycle();
    ethphy_mdio_w_write(pins);return received;
}
static void bits(unsigned value,unsigned count) {while(count)bit(1,(value>>(--count))&1u);}
static void header(unsigned address,unsigned reg,unsigned read) {
    bits(~0u,32);bits(1,2);bits(read?2:1,2);bits(address,5);bits(reg,5);
}
hal_result_t hal_eth_mdio_read(unsigned address,unsigned reg,uint16_t *value) {
    if(address>31 || reg>31 || !value)return HAL_INVALID;
    header(address,reg,1);bit(0,1);unsigned ack=bit(0,1),data=0;
    for(unsigned i=0;i<16;++i)data=(data<<1)|bit(0,1);
    bit(0,1);ethphy_mdio_w_write(0);*value=data;
    if(ack) {if(info.initialized)++info.mdio_errors;return HAL_IO;}
    return HAL_OK;
}
hal_result_t hal_eth_mdio_write(unsigned address,unsigned reg,uint16_t value) {
    if(address>31 || reg>31)return HAL_INVALID;
    header(address,reg,0);bits(2,2);bits(value,16);bit(0,1);ethphy_mdio_w_write(0);return HAL_OK;
}
static hal_result_t read_reg(unsigned reg,uint16_t *value) {return hal_eth_mdio_read(info.phy_address,reg,value);}
static void write_reg(unsigned reg,uint16_t value) {hal_eth_mdio_write(info.phy_address,reg,value);}
#ifndef CONFIG_ETH_RING_DMA
static void irq(void *unused) {
    (void)unused;++info.irqs;
    /* RX W1C releases the slot: keep ownership until receive has copied it. */
    if(ethmac_sram_writer_ev_pending_read()&1u)ethmac_sram_writer_ev_enable_write(0);
    if(ethmac_sram_reader_ev_pending_read()&1u) {
        ethmac_sram_reader_ev_pending_write(1);tx_busy=0;
    }
}
#endif
void hal_eth_stop(void) {
#ifdef CONFIG_ETH_RING_DMA
    eth_dma_control_write(0);eth_dma_ev_enable_write(0);hal_irq_enable(ETH_DMA_INTERRUPT,0);
    uint32_t deadline=hal_time_ms()+100;
    while(eth_dma_busy_read() && !hal_deadline_reached(hal_time_ms(),deadline)) {}
    rx_borrowed=tx_borrowed=0;
#endif
    info.initialized=info.link=info.speed_mbps=info.full_duplex=0;
    ethmac_sram_writer_ev_enable_write(0);ethmac_sram_reader_ev_enable_write(0);
#ifndef CONFIG_ETH_RING_DMA
    hal_irq_enable(ETHMAC_INTERRUPT,0);tx_busy=0;
#endif
    ethphy_crg_reset_write(1);
}
static uint32_t ref_count(void) {
    uint32_t value=eth_clock_gray_read();value^=value>>16;value^=value>>8;
    value^=value>>4;value^=value>>2;return value^(value>>1);
}
hal_result_t hal_eth_init(void) {
    hal_eth_stop();info=(hal_eth_info_t){0};
#ifndef CONFIG_ETH_RING_DMA
    tx_slot=0;
#endif
    uint32_t flash_id;unsigned uid_length;
    if(hal_flash_probe(&flash_id,0)!=HAL_OK || hal_flash_uid(info.flash_uid,&uid_length)!=HAL_OK)return HAL_IO;
    info.uid_length=uid_length;
    /* FNV-1a64 of UID bytes in wire order, then low six bytes. Clear group
       bit and set local bit; never fall back to a shared hardcoded address. */
    uint64_t hash=UINT64_C(14695981039346656037);
    for(unsigned i=0;i<info.uid_length;++i) {hash^=info.flash_uid[i];hash*=UINT64_C(1099511628211);}
    for(unsigned i=0;i<6;++i)info.mac[i]=hash>>(8*i);
    info.mac[0]=(info.mac[0]&0xfeu)|2u;
    uint16_t id1=0,id2=0;unsigned address=0,scan;
    /* Address zero can be the PHY's broadcast alias: prefer individual 1..31. */
    for(scan=0;scan<32;++scan) {
        address=(scan+1)&31u;
        if(hal_eth_mdio_read(address,2,&id1)==HAL_OK &&
           hal_eth_mdio_read(address,3,&id2)==HAL_OK && id1==0x001c && (id2&0xfff0)==0xc810)break;
    }
    if(scan==32)return HAL_NO_MEDIA;
    info.phy_address=address;info.phy_id=((unsigned)id1<<16)|id2;
    write_reg(31,0);write_reg(0,0x8000);uint32_t start=hal_time_ms();uint16_t value;
    do {
        if(read_reg(0,&value)!=HAL_OK)return HAL_IO;
        if(hal_time_ms()-start>=500)return HAL_TIMEOUT;
    } while(value&0x8000);
    /* Preserve vendor timing offsets. REF_CLK is output, CRS_DV normal,
       RMII data without SSD error. Disable clock-stopping power saving/EEE. */
    write_reg(31,7);if(read_reg(16,&value)!=HAL_OK)return HAL_IO;
    info.rmii=(value|8u)&~0x1006u;write_reg(16,info.rmii);
    write_reg(24,1); /* disable REF_CLK spread spectrum */
    write_reg(31,0);if(read_reg(24,&value)!=HAL_OK)return HAL_IO;write_reg(24,value&~0x8000u);
    write_reg(13,7);write_reg(14,60);write_reg(13,0x4007);write_reg(14,0); /* MMD7 EEE advertisement */
    write_reg(4,0x0101);write_reg(0,0x1200); /* advertise only 100M full duplex */
    uint32_t before=ref_count(),ticks=hal_ticks();
    while((uint32_t)(hal_ticks()-ticks)<CONFIG_CLOCK_FREQUENCY/100u) {}
    uint32_t elapsed=hal_ticks()-ticks,delta=ref_count()-before;
    info.ref_clock_hz=(uint32_t)(((uint64_t)delta*CONFIG_CLOCK_FREQUENCY)/elapsed);
    if(info.ref_clock_hz<49000000u || info.ref_clock_hz>51000000u)return HAL_IO;
    ethphy_crg_reset_write(0);
#ifdef CONFIG_ETH_RING_DMA
    ethmac_sram_reader_ev_enable_write(0);ethmac_sram_writer_ev_enable_write(0);ring_init();
#else
    hal_irq_attach(ETHMAC_INTERRUPT,irq,0);hal_irq_enable(ETHMAC_INTERRUPT,1);
    ethmac_sram_reader_ev_pending_write(1);
    ethmac_sram_reader_ev_enable_write(1);ethmac_sram_writer_ev_enable_write(1);
#endif
    info.initialized=1;next_poll=hal_time_ms();hal_eth_poll();return HAL_OK;
}
void hal_eth_poll(void) {
    if(!info.initialized || !hal_deadline_reached(hal_time_ms(),next_poll))return;
    next_poll=hal_time_ms()+250;uint16_t status,control,partner;
    if(read_reg(1,&status)!=HAL_OK || read_reg(1,&status)!=HAL_OK ||
       read_reg(0,&control)!=HAL_OK || read_reg(5,&partner)!=HAL_OK)return;
    unsigned link=(status&0x24u)==0x24u && (control&0x2100u)==0x2100u && (partner&0x100u);
    link=!!link;if(link!=info.link)++info.link_changes;
    info.link=link;info.speed_mbps=link?100:0;info.full_duplex=link;
    info.bmcr=control;info.bmsr=status;info.partner=partner;
}
void hal_eth_get_info(hal_eth_info_t *out) {
    if(!out)return;
    unsigned state=hal_irq_save();*out=info;
#ifndef CONFIG_ETH_RING_DMA
    out->tx_busy=tx_busy;
#endif
    hal_irq_restore(state);
#ifdef CONFIG_ETH_RING_DMA
    out->dma_rx=eth_dma_rx_packets_read();out->dma_tx=eth_dma_tx_packets_read();
    out->tx_busy=(uint16_t)(tx_producer-(uint16_t)eth_dma_tx_consumer_read())!=0;
#endif
    out->rx_drops=ethmac_sram_writer_errors_read();out->crc_errors=ethmac_rx_datapath_crc_errors_read();
    out->preamble_errors=ethmac_rx_datapath_preamble_errors_read();
}
hal_result_t hal_eth_get_mac(uint8_t mac[6]) {
    if(!mac)return HAL_INVALID;
    if(!info.uid_length)return HAL_NO_MEDIA;
    for(unsigned i=0;i<6;++i)mac[i]=info.mac[i];
    return HAL_OK;
}
hal_result_t hal_eth_send(const void *frame,unsigned length) {
    if(!frame || length<14 || length>HAL_ETH_MAX_FRAME)return HAL_INVALID;
    if(!info.initialized || !info.link)return HAL_NO_MEDIA;
#ifdef CONFIG_ETH_RING_DMA
    void *destination;unsigned capacity;
    hal_result_t result=hal_eth_tx_acquire(&destination,&capacity);
    if(result!=HAL_OK)return result;
    memcpy(destination,frame,length);return hal_eth_tx_commit(destination,length);
#else
    if(tx_busy || !ethmac_sram_reader_ready_read())return HAL_BUSY;
    volatile uint32_t *slot=(volatile uint32_t *)(ETHMAC_TX_BASE+tx_slot*ETHMAC_SLOT_SIZE);
    const uint8_t *bytes=frame;
    for(unsigned offset=0;offset<length;offset+=4) {
        unsigned word=0;for(unsigned b=0;b<4 && offset+b<length;++b)word|=(unsigned)bytes[offset+b]<<(8*b);
        slot[offset/4]=word;
    }
    unsigned state=hal_irq_save();tx_busy=1;
    ethmac_sram_reader_slot_write(tx_slot);ethmac_sram_reader_length_write(length);
    __asm__ volatile("fence iorw,iorw":::"memory");ethmac_sram_reader_start_write(1);
    tx_slot^=1;++info.tx_frames;hal_irq_restore(state);return HAL_OK;
#endif
}
hal_result_t hal_eth_receive(void *frame,unsigned capacity,unsigned *length) {
    if(!frame || !length)return HAL_INVALID;
    *length=0;if(!info.initialized)return HAL_NO_MEDIA;
#ifdef CONFIG_ETH_RING_DMA
    const void *source;unsigned size;
    hal_result_t result=hal_eth_rx_acquire(&source,&size);
    if(result!=HAL_OK)return result;
    if(size>capacity) {hal_eth_rx_release(source);return HAL_INVALID;}
    memcpy(frame,source,size);*length=size;return hal_eth_rx_release(source);
#else
    if(!(ethmac_sram_writer_ev_pending_read()&1u))return HAL_BUSY;
    unsigned size=ethmac_sram_writer_length_read(),index=ethmac_sram_writer_slot_read();
    hal_result_t result=HAL_INVALID;
    if(size<=capacity && size>=14 && size<=HAL_ETH_MAX_FRAME && index<ETHMAC_RX_SLOTS) {
        volatile const uint32_t *slot=(volatile const uint32_t *)(ETHMAC_RX_BASE+index*ETHMAC_SLOT_SIZE);
        uint8_t *bytes=frame;
        for(unsigned offset=0;offset<size;offset+=4) {
            unsigned word=slot[offset/4];for(unsigned b=0;b<4 && offset+b<size;++b)bytes[offset+b]=word>>(8*b);
        }
        *length=size;++info.rx_frames;result=HAL_OK;
    }
    ethmac_sram_writer_ev_pending_write(1);ethmac_sram_writer_ev_enable_write(1);return result;
#endif
}
