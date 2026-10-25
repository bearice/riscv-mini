/* Native LiteSDCard commands; hard ATCDMAC300 moves FIFO words to DDR. */
#include "ae350_sd.h"
#include "ae350_dma.h"
#include "ae350_address_map.h"
#define W(a) (*(volatile uint32_t *)(uintptr_t)(a))
#define SD(o) W(AE350_SD_BASE+(o))
enum { SHORT=1,LONG=2,BUSY=3,CRC=4,READ=32 };
static struct ae350_sd_info info;
static unsigned channel, owned, ready, rca, hc, source_hz, high_speed;
static _Alignas(32) uint8_t bounce[4096];
static uint32_t ticks(void) {uint32_t n;__asm__ volatile("rdcycle %0":"=r"(n));return n;}
static void delay(unsigned ms) {uint32_t t=ticks();while((uint32_t)(ticks()-t)<(AE350_CPU_HZ/1000u)*ms) {}}
static int present(void) {return !(SD(0x40)&1);}
static uint32_t response(unsigned word) {return SD(0x98-word*4);}
static int event(unsigned data) {
    uint32_t start=ticks();
    do {uint32_t e=SD(data?0xa0:0x9c);if(e&1)return !(e&14);if(!present())return 0;}
    while((uint32_t)(ticks()-start)<AE350_CPU_HZ);
    return 0;
}
static int command(unsigned cmd,uint32_t arg,unsigned flags) {
    if(!present())return 0;
    info.command=cmd;SD(0x80)=arg;SD(0x84)=(cmd<<8)|flags;SD(0x88)=1;
    int ok=event(0);info.response=response(0);return ok;
}
static int checked(unsigned cmd,uint32_t arg,unsigned flags) {
    return command(cmd,arg,flags) && !(response(0)&0xfff9a008u);
}
int ae350_sd_stop(void) {
    SD(0xc)=0; /* Stop requesting; retain FIFO data until the channel drains. */
    if(owned) {
        int rc=ae350_dma_abort_drain(channel);
        if(rc)return rc;
        owned=0;
    }
    SD(0xc)=1;delay(1);SD(0xc)=0;delay(1);ready=0;
    return 0;
}
static int start_dma(unsigned bytes) {
    int rc=ae350_dma_sync_for_device((uintptr_t)bounce,sizeof(bounce));if(rc)return rc;
    struct ae350_dma_req req={.device=AE350_DMAC_ID,.abi=AE350_ABI_VERSION,
        .csr_base=AE350_DMAC_BASE,.irq_id=AE350_IRQ_ID,.need_cap=AE350_CAP_SD,
        .channel=channel,.src=AE350_SD_RX_DATA,.dst=(uintptr_t)bounce,.bytes=bytes,
        .width_bytes=4,.src_mode=2,.dst_mode=0,.handshake=6};
    rc=ae350_dma_submit(&req);if(rc)return rc;owned=1;SD(0xc)=2;return 0;
}
static int finish_dma(unsigned bytes) {
    uint32_t start=ticks();int rc;
    do {rc=ae350_dma_poll(channel);if(rc!=AE350_DMA_ERR_BUSY)break;}
    while((uint32_t)(ticks()-start)<AE350_CPU_HZ);
    SD(0xc)=0;
    if(rc)return rc;
    owned=0;
    return ae350_dma_sync_for_cpu((uintptr_t)bounce,bytes);
}
static unsigned bits(unsigned low,unsigned count) {
    unsigned word=low/32,shift=low%32;uint64_t value=response(word);
    if(shift+count>32)value|=(uint64_t)response(word+1)<<32;
    return (value>>shift)&((1u<<count)-1);
}
int ae350_sd_init(unsigned selected) {
    if(owned || selected>7 || ae350_dma_busy(selected))return AE350_DMA_ERR_BUSY;
    channel=selected;
    volatile struct ae350_sd_info *clear=&info;
    clear->sectors=0;clear->hz=0;clear->width=0;clear->reads=0;clear->error=0;clear->command=0;clear->response=0;
    if(SD(0)!=AE350_SD_ID || SD(4)!=AE350_ABI_VERSION || SD(8)!=AE350_SD_CAP)return AE350_DMA_ERR_DEVICE;
    int rc=ae350_sd_stop();if(rc)return rc;
    if(!present())goto fail;
    source_hz=SD(0x20);high_speed=0;
    if(source_hz<800000u)goto fail;
    SD(0x44)=(source_hz+399999u)/400000u;SD(0x48)=0;
    SD(0x50)=source_hz/4;SD(0x54)=source_hz/2;
    info.hz=source_hz/(2*((SD(0x44)+1)/2));info.width=1;
    SD(0x4c)=1;delay(2);
    if(!command(0,0,0))goto fail;
    unsigned v2=command(8,0x1aa,SHORT|CRC);
    if(v2 && (response(0)&0xfff)!=0x1aa)goto fail;
    uint32_t start=ticks(),ocr=0;
    do {
        if(!checked(55,0,SHORT|CRC) || !command(41,0x00ff8000u|(v2?0x40000000u:0),SHORT))goto fail;
        ocr=response(0);if(ocr&0x80000000u)break;delay(10);
    }while((uint32_t)(ticks()-start)<2u*AE350_CPU_HZ);
    if(!(ocr&0x80000000u) || !(ocr&0x00ff8000u))goto fail;
    hc=!!(ocr&0x40000000u);
    if(!command(2,0,LONG|CRC) || !command(3,0,SHORT|CRC) || (response(0)&0xe000))goto fail;
    rca=response(0)>>16;if(!rca || !command(9,rca<<16,LONG|CRC))goto fail;
    unsigned version=bits(126,2);
    if(version==1) {unsigned size=bits(48,22);if(size==0x3fffff)goto fail;info.sectors=(size+1)*1024;}
    else if(version==0) {
        unsigned shift=bits(80,4)+bits(47,3)+2;
        uint64_t total=((uint64_t)(bits(62,12)+1)<<shift)>>9;
        if(!total || total>0xffffffffu)goto fail;
        info.sectors=total;
    }else goto fail;
    if(!checked(7,rca<<16,BUSY|CRC))goto fail;
    delay(10);
    if(!hc && !checked(16,512,SHORT|CRC))goto fail;
    if(!checked(55,rca<<16,SHORT|CRC))goto fail;
    SD(0xa4)=8;SD(0xa8)=1;
    if(start_dma(8) || !checked(51,0,SHORT|CRC|READ) || !event(1) || finish_dma(8))goto fail;
    if(!(bounce[1]&4) || !checked(55,rca<<16,SHORT|CRC) || !checked(6,2,SHORT|CRC))goto fail;
    SD(0x48)=1;info.width=4;
    SD(0x44)=(source_hz+9999999u)/10000000u;delay(1);
    info.hz=source_hz/(2*((SD(0x44)+1)/2));ready=1;return 0;
fail:
    info.error=1u | ((SD(0x9c)&15u)<<8) | ((SD(0xa0)&15u)<<12) | ((SD(0x10)&1u)<<16);
    rc=ae350_sd_stop();return rc?rc:-1;
}
/* Default Speed is capped at 25MHz. Above it, query and select High Speed
 * with CMD6, verify the returned function, then change the host divider. */
int ae350_sd_set_clock(unsigned hz) {
    if(!ready || !present() || owned)return -3;
    if(!hz || hz>50000000u || hz>source_hz/2u)return -2;
    if(hz>25000000u && !high_speed) {
        for(unsigned mode=0;mode<2;mode++) {
            SD(0xa4)=64;SD(0xa8)=1;
            if(start_dma(64) || !checked(6,(mode?0x80fffff1u:0x00fffff1u),SHORT|CRC|READ) || !event(1) || finish_dma(64))goto fail;
            if(!mode && !(bounce[13]&2u))return -4;
            if(mode && (bounce[16]&15u)!=1u)goto fail;
        }
        high_speed=1;
    }
    unsigned divider=(source_hz+hz-1u)/hz;
    if(divider<2u || divider>511u)return -2;
    SD(0x44)=divider;delay(1);
    info.hz=source_hz/(2*((SD(0x44)+1)/2));
    return 0;
fail:
    info.error++;int rc=ae350_sd_stop();return rc?rc:-1;
}

int ae350_sd_read(uint32_t sector,void *buffer,unsigned count) {
    if(!buffer || !count || sector>=info.sectors || count>info.sectors-sector)return -2;
    if(!ready || !present())return -3;
    uint8_t *dst=buffer;
    while(count) {
        unsigned blocks=count>8?8:count,bytes=blocks*512;
        SD(0xa4)=512;SD(0xa8)=blocks;
        if(start_dma(bytes) || !checked(blocks==1?17:18,hc?sector:sector*512,SHORT|CRC|READ) || !event(1) || finish_dma(bytes))goto fail;
        if(blocks>1 && !checked(12,0,BUSY|CRC))goto fail;
        for(unsigned i=0;i<bytes;i++)dst[i]=bounce[i];
        dst+=bytes;sector+=blocks;count-=blocks;info.reads+=blocks;
    }
    return 0;
fail:
    info.error++;int rc=ae350_sd_stop();return rc?rc:-1;
}
void ae350_sd_info(struct ae350_sd_info *out) {
    volatile struct ae350_sd_info *dst=out;
    dst->sectors=info.sectors;dst->hz=info.hz;dst->width=info.width;dst->reads=info.reads;
    dst->error=info.error;dst->command=info.command;dst->response=info.response;
}
