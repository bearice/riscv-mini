#include "ae350_video.h"
#include "ae350_address_map.h"
#include "ae350_dma.h"

#ifdef AE350_VIDEO_HOST_TEST
uint32_t ae350_video_test_read(unsigned off);
void ae350_video_test_write(unsigned off,uint32_t value);
uint32_t ae350_video_test_cycle(void);
int ae350_video_test_clean(uint32_t base,uint32_t bytes);
#define R(off) ae350_video_test_read(off)
#define W(off,value) ae350_video_test_write(off,value)
#define cycle() ae350_video_test_cycle()
#define clean(base,bytes) ae350_video_test_clean(base,bytes)
#define fence() ((void)0)
#else
#define R(off) (*(volatile uint32_t *)(uintptr_t)(AE350_VIDEO_BASE+(off)))
#define W(off,value) (R(off)=(value))
static uint32_t cycle(void) {uint32_t value;__asm__ volatile("csrr %0,mcycle":"=r"(value));return value;}
#define clean(base,bytes) ae350_dma_sync_for_device(base,bytes)
#define fence() __asm__ volatile("fence rw,rw" ::: "memory")
#endif

static uint32_t bases[2],frame_bytes,frame_stride;
static unsigned configured;
int ae350_video_get_hpd(unsigned *connected) {
    if(!connected)return AE350_VIDEO_RANGE;
    if(R(0)!=AE350_VIDEO_ID || !(R(8)&0x80u))return AE350_VIDEO_DEVICE;
    /* Require both filtered and current synchronized levels for readiness. */
    *connected=(R(VIDEO_HPD)&3u)==3u;
    return AE350_VIDEO_OK;
}
static int hpd_irq_device(void) {
    return R(0)==AE350_VIDEO_ID && (R(8)&0x100u);
}
int ae350_video_hpd_events(unsigned *events) {
    if(!events)return AE350_VIDEO_RANGE;
    if(!hpd_irq_device())return AE350_VIDEO_DEVICE;
    *events=R(VIDEO_IRQ_STATUS)&AE350_VIDEO_HPD_EVENTS;
    return AE350_VIDEO_OK;
}
int ae350_video_hpd_irq_enable(unsigned mask) {
    if(mask&~AE350_VIDEO_HPD_EVENTS)return AE350_VIDEO_RANGE;
    if(!hpd_irq_device())return AE350_VIDEO_DEVICE;
    W(VIDEO_IRQ_ENABLE,mask);fence();
    return AE350_VIDEO_OK;
}
int ae350_video_hpd_ack(unsigned events) {
    if(events&~AE350_VIDEO_HPD_EVENTS)return AE350_VIDEO_RANGE;
    if(!hpd_irq_device())return AE350_VIDEO_DEVICE;
    W(VIDEO_IRQ_STATUS,events);fence();
    return AE350_VIDEO_OK;
}
static unsigned current_format(void) {return (R(8)&0x40u) ? R(VIDEO_FORMAT):0;}

unsigned ae350_video_mode_count(void) {
    return R(0)==AE350_VIDEO_ID && (R(8)&0x40u) ? R(VIDEO_MODE_COUNT):0;
}
int ae350_video_get_mode(unsigned mode,struct ae350_video_mode *s) {
    if(!s)return AE350_VIDEO_RANGE;
    if(mode==AE350_VIDEO_MODE_CUSTOM) {
        /* Read back the programmable custom mode from the CUS bank. */
        uint32_t wh=R(VIDEO_CUS_WH),h=R(VIDEO_CUS_H),v=R(VIDEO_CUS_V);
        s->width=wh&4095u;s->height=(wh>>16)&4095u;
        s->h_front=h&4095u;s->h_sync=(h>>12)&4095u;s->h_back=h>>24;
        s->v_front=v&255u;s->v_sync=(v>>8)&255u;s->v_back=(v>>16)&255u;
        s->h_positive=(v>>24)&1u;s->v_positive=(v>>25)&1u;
        s->h_total=s->width+s->h_front+s->h_sync+s->h_back;
        s->v_total=s->height+s->v_front+s->v_sync+s->v_back;
        s->pixel_hz=R(VIDEO_CUS_PCLK);s->nominal_pixel_hz=s->pixel_hz;
        s->refresh_millihz=(uint32_t)(((uint64_t)s->pixel_hz*1000u)/
            ((uint64_t)s->h_total*s->v_total));
        return AE350_VIDEO_OK;
    }
    if(mode>=ae350_video_mode_count())return AE350_VIDEO_RANGE;
    unsigned off=VIDEO_MODE_INFO_BASE+mode*32u;
    uint32_t geometry=R(off),totals=R(off+8),h=R(off+12),v=R(off+16),flags=R(off+20);
    s->width=geometry&65535u;s->height=geometry>>16;s->pixel_hz=R(off+4);
    s->h_total=totals&65535u;s->v_total=totals>>16;
    s->h_front=h&1023u;s->h_sync=(h>>10)&1023u;s->h_back=h>>20;
    s->v_front=v&1023u;s->v_sync=(v>>10)&1023u;s->v_back=v>>20;
    s->h_positive=flags&1u;s->v_positive=(flags>>1)&1u;
    s->refresh_millihz=R(off+24);s->nominal_pixel_hz=R(off+28);
    return AE350_VIDEO_OK;
}

int ae350_video_enable(unsigned enable) {
    if(enable>1)return AE350_VIDEO_RANGE;
    if(R(0)!=AE350_VIDEO_ID || !(R(8)&0x40u))return AE350_VIDEO_DEVICE;
    if(R(VIDEO_MODE_STATUS) || R(VIDEO_COMMIT))return AE350_VIDEO_BUSY;
    if(!enable) {
        int error=ae350_video_stop();
        if(error)return error;
    } else if(!(R(VIDEO_STATUS)&16u))return AE350_VIDEO_BUSY;
    W(VIDEO_ENABLE,enable);fence();
    return R(VIDEO_ENABLE)==enable ? AE350_VIDEO_OK:AE350_VIDEO_STATE;
}

int ae350_video_set_mode(unsigned mode,enum ae350_video_format format) {
    if(mode>=ae350_video_mode_count() || (unsigned)format>3u)return AE350_VIDEO_RANGE;
    int error=ae350_video_enable(0);
    if(error)return error;
    W(VIDEO_MODE_CONFIG,mode|((unsigned)format<<8));fence();
    configured=0;
    uint32_t start=cycle();
    while(R(VIDEO_MODE_STATUS)&1u)
        if((uint32_t)(cycle()-start)>400000000u)return AE350_VIDEO_TIMEOUT;
    return R(VIDEO_MODE)==mode && R(VIDEO_FORMAT)==(unsigned)format ? AE350_VIDEO_OK:AE350_VIDEO_STATE;
}

int ae350_video_set_custom_mode(unsigned width, unsigned height,
    unsigned h_front, unsigned h_sync, unsigned h_back,
    unsigned v_front, unsigned v_sync, unsigned v_back,
    unsigned refresh_millihz, unsigned h_positive, unsigned v_positive,
    enum ae350_video_format format) {
    if((unsigned)format>3u || h_positive>1u || v_positive>1u || h_back>255u ||
       v_front>255u || v_sync>255u || v_back>255u)return AE350_VIDEO_RANGE;
    unsigned h_total=width+h_front+h_sync+h_back;
    unsigned v_total=height+v_front+v_sync+v_back;
    if(!width || !height || h_total>=4096u || v_total>=4096u ||
       width>4095u || height>4095u || h_front>4095u || h_sync>4095u) return AE350_VIDEO_RANGE;
    /* pixel clock from refresh: total * refresh(mHz) / 1000 */
    uint64_t pixel=(uint64_t)h_total*v_total*refresh_millihz/1000u;
    if(pixel==0 || pixel>74250000u)return AE350_VIDEO_RANGE;
    /* PLL search (mirrors video_modes.pll_config): integer dividers keep
     * the exact serializer 5:1 relationship; MDIV has eighth steps. */
    int best=-1;unsigned best_idiv=0,best_m8=0,best_ser=0;
    for(unsigned idiv=1;idiv<=2;idiv++)for(unsigned ser=2;ser<=25;ser++) {
        unsigned pixdiv=ser*5u;
        uint64_t m8=(pixel*pixdiv*idiv*8u+25000000u)/50000000u;
        uint64_t vco=(uint64_t)50000000u*m8;
        uint64_t lo=(uint64_t)650000000u*idiv*8u,hi=(uint64_t)1300000000u*idiv*8u;
        if(m8<16 || m8>1023 || vco<lo || vco>hi)continue;
        /* error in ppm-ish (actual vs target), avoid div by zero */
        uint64_t scaled=(uint64_t)pixel*idiv*8u*pixdiv;
        int64_t err=(int64_t)vco-(int64_t)scaled;
        if(err<0)err=-err;
        uint64_t tol=(uint64_t)pixel*idiv*8u*pixdiv/200u; /* 0.5% */
        if((uint64_t)err>tol)continue;
        if(best<0 || err<best) {best=err;best_idiv=idiv;best_m8=(unsigned)m8;best_ser=ser;}
    }
    if(best<0)return AE350_VIDEO_RANGE;
    unsigned pixdiv=best_ser*5u;
    int error=ae350_video_enable(0);
    if(error)return error;
    W(VIDEO_CUS_WH,width|((height&4095u)<<16));
    W(VIDEO_CUS_H,h_front|((h_sync&4095u)<<12)|((h_back&255u)<<24));
    W(VIDEO_CUS_V,v_front|((v_sync&255u)<<8)|((v_back&255u)<<16)|
        ((h_positive&1u)<<24)|((v_positive&1u)<<25));
    W(VIDEO_CUS_PLL,best_idiv|((best_m8&1023u)<<2)|((pixdiv&127u)<<12)|((best_ser&127u)<<19));
    W(VIDEO_CUS_PCLK,(uint32_t)pixel);fence();
    W(VIDEO_MODE_CONFIG,AE350_VIDEO_MODE_CUSTOM|((unsigned)format<<8));fence();
    configured=0;
    uint32_t start=cycle();
    while(R(VIDEO_MODE_STATUS)&1u)
        if((uint32_t)(cycle()-start)>400000000u)return AE350_VIDEO_TIMEOUT;
    return R(VIDEO_MODE)==AE350_VIDEO_MODE_CUSTOM && R(VIDEO_FORMAT)==(unsigned)format ? AE350_VIDEO_OK:AE350_VIDEO_STATE;
}

static int wait_pending(void) {
    uint32_t start=cycle();
    while(R(VIDEO_COMMIT))
        if((uint32_t)(cycle()-start)>400000000u)return AE350_VIDEO_TIMEOUT;
    return AE350_VIDEO_OK;
}

static int commit(unsigned control) {
    int error=wait_pending();
    if(error)return error;
    uint32_t sequence=R(VIDEO_SEQUENCE)+1u;
    W(VIDEO_CONTROL,control);W(VIDEO_COMMIT,1);fence();
    if(R(VIDEO_SEQUENCE)!=sequence)return AE350_VIDEO_BUSY;
    error=wait_pending();
    if(error)return error;
    return R(VIDEO_ACTIVE_SEQUENCE)==sequence ? AE350_VIDEO_OK:AE350_VIDEO_STATE;
}

int ae350_video_stop(void) {
    if(R(0)!=AE350_VIDEO_ID || R(4)!=AE350_ABI_VERSION)return AE350_VIDEO_DEVICE;
    int error=commit(0);
    if(error)return error;
    uint32_t start=cycle();
    while(!(R(VIDEO_STATUS)&16u))
        if((uint32_t)(cycle()-start)>400000000u)return AE350_VIDEO_TIMEOUT;
    fence();return AE350_VIDEO_OK;
}

int ae350_video_init_buffers(void *first,void *second,uint32_t stride) {
    if(R(0)!=AE350_VIDEO_ID || R(4)!=AE350_ABI_VERSION || (R(8)&15u)!=15u)return AE350_VIDEO_DEVICE;
    uint32_t geometry=R(VIDEO_GEOMETRY),width=geometry&65535u,height=geometry>>16;
    if(!width || !height || width>4096 || height>4096)return AE350_VIDEO_DEVICE;
    if((R(8)&0x40u) && R(VIDEO_MODE_STATUS))return AE350_VIDEO_BUSY;
    unsigned format=current_format();
    if(format>2)return AE350_VIDEO_DEVICE;
    unsigned row_bytes=width*(format+2u);
    if(!stride)stride=(row_bytes+31u)&~31u;
    if((stride&31u) || stride<row_bytes || stride>=65536u)return AE350_VIDEO_ALIGN;
    uint32_t bytes=stride*height,a=(uint32_t)(uintptr_t)first,b=(uint32_t)(uintptr_t)second;
    if((a&31u) || (b&31u))return AE350_VIDEO_ALIGN;
    const uint32_t image_end=AE350_DDR_CODE+AE350_DDR_CODE_MAX;
    if(a<image_end || b<image_end || bytes>AE350_DDR_SIZE ||
       a>AE350_DDR_SIZE-bytes || b>AE350_DDR_SIZE-bytes ||
       (a<b+bytes && b<a+bytes))return AE350_VIDEO_RANGE;
    int error=ae350_video_stop();
    if(error)return error;
    W(VIDEO_BASE0,a);W(VIDEO_BASE1,b);W(VIDEO_STRIDE,stride);W(VIDEO_CLEAR_ERRORS,1);
    bases[0]=a;bases[1]=b;frame_bytes=bytes;frame_stride=stride;configured=1;
    return AE350_VIDEO_OK;
}

int ae350_video_init(uint16_t *first,uint16_t *second,uint32_t stride) {
    if(current_format()!=AE350_VIDEO_RGB565)return AE350_VIDEO_STATE;
    return ae350_video_init_buffers(first,second,stride);
}

uint16_t *ae350_video_frame(unsigned slot) {
    if(current_format()!=AE350_VIDEO_RGB565)return 0;
    return ae350_video_frame_data(slot);
}

void *ae350_video_frame_data(unsigned slot) {
    if(!configured || slot>1 || R(VIDEO_COMMIT))return 0;
    uint32_t status=R(VIDEO_STATUS);
    if(status&1u) {if(((status>>1)&1u)==slot)return 0;}
    else if(!(status&16u))return 0;
    return (void *)(uintptr_t)bases[slot];
}

int ae350_video_present(unsigned slot) {
    if(!configured || slot>1)return AE350_VIDEO_STATE;
    int error=wait_pending();
    if(error)return error;
    uint32_t status=R(VIDEO_STATUS);
    if((status&1u) && ((status>>1)&1u)==slot)return AE350_VIDEO_OK;
    if(!ae350_video_frame_data(slot))return AE350_VIDEO_BUSY;
    if(clean(bases[slot],frame_bytes)!=AE350_DMA_OK)return AE350_VIDEO_CACHE;
    if((R(8)&0x40u) && !R(VIDEO_ENABLE)) {
        error=ae350_video_enable(1);
        if(error)return error;
    }
    fence();error=commit(1u|(slot<<1));
    if(error)return error;
    status=R(VIDEO_STATUS);
    return (status&3u)==(1u|(slot<<1)) && R(VIDEO_ACTIVE_BASE)==bases[slot] ?
        AE350_VIDEO_OK:AE350_VIDEO_STATE;
}

int ae350_video_status(struct ae350_video_status *s) {
    if(!s || R(0)!=AE350_VIDEO_ID || R(4)!=AE350_ABI_VERSION)return AE350_VIDEO_DEVICE;
    uint32_t geometry=R(VIDEO_GEOMETRY),status=R(VIDEO_STATUS);
    s->mode=(R(8)&0x40u)?R(VIDEO_MODE):0;
    s->output_enabled=(R(8)&0x40u)?R(VIDEO_ENABLE):1;
    s->format=current_format();s->mode_busy=(R(8)&0x40u)?R(VIDEO_MODE_STATUS):0;
    s->width=geometry&65535u;s->height=geometry>>16;s->stride=frame_stride;
    s->pixel_hz=R(VIDEO_PIXEL_HZ);s->enabled=status&1u;s->active=(status>>1)&1u;
    s->pending=R(VIDEO_COMMIT);s->stopped=(status>>4)&1u;s->active_base=R(VIDEO_ACTIVE_BASE);
    s->frames=R(VIDEO_FRAMES);s->underflows=R(VIDEO_UNDERFLOWS);s->missing=R(VIDEO_MISSING);
    s->address_error=R(VIDEO_ADDRESS_ERROR);s->timeout_error=R(VIDEO_TIMEOUT_ERROR);
    s->errors=R(VIDEO_ERRORS);s->fifo_min=R(VIDEO_FIFO_MIN);s->max_wait=R(VIDEO_MAX_WAIT);
    s->reads=R(VIDEO_READS);s->cpu_commands=R(VIDEO_CPU_COMMANDS);
    s->request_overflow=R(VIDEO_REQUEST_OVERFLOW);return AE350_VIDEO_OK;
}
