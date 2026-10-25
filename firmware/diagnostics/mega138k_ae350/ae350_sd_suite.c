/* First SD gate: no filesystem or media writes. CRCs are observations until
 * matched against an independent host reference for this exact card. */
#include <stdint.h>
#include "ae350_sd.h"
#include "ae350_dma.h"
#include "ae350_address_map.h"
static uint8_t data[4096+32] __attribute__((aligned(32)));
static volatile uint32_t *const r=(volatile uint32_t *)AE350_RESULTS_BASE;
static uint32_t crc32(const uint8_t *p,unsigned n) {
    uint32_t c=~0u;while(n--) {c^=*p++;for(unsigned i=0;i<8;i++)c=(c>>1)^(0xedb88320u&-(c&1));}return ~c;
}
void main(void) {
    for(unsigned i=0;i<32;i++)r[i]=0;
    r[0]=0x53440001;r[31]=0x53440001;
    int rc=ae350_sd_init(2);r[1]=rc;
    struct ae350_sd_info info;
    if(!rc) {
        rc=ae350_sd_read(0,data,1);r[2]=rc;
        if(!rc) {r[3]=crc32(data,512);r[4]=data[510]|(data[511]<<8);}
        for(unsigned i=0;i<sizeof(data);i++)data[i]=0xa5;
        rc=ae350_sd_read(0,data+1,8);r[5]=rc;
        if(!rc) {r[6]=crc32(data+1,512);r[7]=crc32(data+1,4096);r[8]=data[0];r[9]=data[4097];}
    }
    if(!rc) {
        /* Qualify TX routing without a card write command: core stays idle,
         * so the bytes remain in the TX staging FIFO/converter. */
        volatile uint32_t *control=(volatile uint32_t *)(AE350_SD_BASE+0xc);
        *control=0;
        ae350_dma_sync_for_device((uintptr_t)data,32);
        struct ae350_dma_req tx={.device=AE350_DMAC_ID,.abi=AE350_ABI_VERSION,
            .csr_base=AE350_DMAC_BASE,.irq_id=AE350_IRQ_ID,.need_cap=AE350_CAP_SD,
            .channel=2,.src=(uintptr_t)data,.dst=AE350_SD_TX_DATA,.bytes=32,
            .width_bytes=4,.src_mode=0,.dst_mode=2,.handshake=5};
        r[18]=ae350_dma_submit(&tx);
        if(!r[18]) {
            for(volatile unsigned n=0;n<20000;n++) {}
            r[19]=ae350_dma_poll(2)==AE350_DMA_ERR_BUSY;
            *control=2;
            r[18]=ae350_dma_wait(2);
            *control=0;
            r[20]=*(volatile uint32_t *)(AE350_SD_BASE+0x24);
            r[21]=ae350_dma_abort_drain(2);
        }
    }
    ae350_sd_info(&info);
    r[10]=info.sectors;r[11]=info.hz;r[12]=info.width;r[13]=info.reads;
    r[14]=info.error;r[15]=info.command;r[16]=info.response;
    r[17]=ae350_sd_stop();
    __asm__ volatile("fence":::"memory");r[0]=0x5344ffff;
    for(;;)__asm__ volatile("wfi");
}
