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

int ae350_video_init(uint16_t *first,uint16_t *second,uint32_t stride) {
    if(R(0)!=AE350_VIDEO_ID || R(4)!=AE350_ABI_VERSION || (R(8)&15u)!=15u)return AE350_VIDEO_DEVICE;
    uint32_t geometry=R(VIDEO_GEOMETRY),width=geometry&65535u,height=geometry>>16;
    if(!width || !height || width>4096 || height>4096)return AE350_VIDEO_DEVICE;
    if(!stride)stride=width*2u;
    if((stride&31u) || stride<width*2u || stride>=65536u)return AE350_VIDEO_ALIGN;
    uint32_t bytes=stride*height,a=(uint32_t)(uintptr_t)first,b=(uint32_t)(uintptr_t)second;
    if((a&31u) || (b&31u))return AE350_VIDEO_ALIGN;
    if(a<0x100000u || b<0x100000u || bytes>AE350_DDR_SIZE ||
       a>AE350_DDR_SIZE-bytes || b>AE350_DDR_SIZE-bytes ||
       (a<b+bytes && b<a+bytes))return AE350_VIDEO_RANGE;
    int error=ae350_video_stop();
    if(error)return error;
    W(VIDEO_BASE0,a);W(VIDEO_BASE1,b);W(VIDEO_STRIDE,stride);W(VIDEO_CLEAR_ERRORS,1);
    bases[0]=a;bases[1]=b;frame_bytes=bytes;frame_stride=stride;configured=1;
    return AE350_VIDEO_OK;
}

uint16_t *ae350_video_frame(unsigned slot) {
    if(!configured || slot>1 || R(VIDEO_COMMIT))return 0;
    uint32_t status=R(VIDEO_STATUS);
    if(status&1u) {if(((status>>1)&1u)==slot)return 0;}
    else if(!(status&16u))return 0;
    return (uint16_t *)(uintptr_t)bases[slot];
}

int ae350_video_present(unsigned slot) {
    if(!configured || slot>1)return AE350_VIDEO_STATE;
    int error=wait_pending();
    if(error)return error;
    uint32_t status=R(VIDEO_STATUS);
    if((status&1u) && ((status>>1)&1u)==slot)return AE350_VIDEO_OK;
    if(!ae350_video_frame(slot))return AE350_VIDEO_BUSY;
    if(clean(bases[slot],frame_bytes)!=AE350_DMA_OK)return AE350_VIDEO_CACHE;
    fence();error=commit(1u|(slot<<1));
    if(error)return error;
    status=R(VIDEO_STATUS);
    return (status&3u)==(1u|(slot<<1)) && R(VIDEO_ACTIVE_BASE)==bases[slot] ?
        AE350_VIDEO_OK:AE350_VIDEO_STATE;
}

int ae350_video_status(struct ae350_video_status *s) {
    if(!s || R(0)!=AE350_VIDEO_ID || R(4)!=AE350_ABI_VERSION)return AE350_VIDEO_DEVICE;
    uint32_t geometry=R(VIDEO_GEOMETRY),status=R(VIDEO_STATUS);
    s->width=geometry&65535u;s->height=geometry>>16;s->stride=frame_stride;
    s->pixel_hz=R(VIDEO_PIXEL_HZ);s->enabled=status&1u;s->active=(status>>1)&1u;
    s->pending=R(VIDEO_COMMIT);s->stopped=(status>>4)&1u;s->active_base=R(VIDEO_ACTIVE_BASE);
    s->frames=R(VIDEO_FRAMES);s->underflows=R(VIDEO_UNDERFLOWS);s->missing=R(VIDEO_MISSING);
    s->address_error=R(VIDEO_ADDRESS_ERROR);s->timeout_error=R(VIDEO_TIMEOUT_ERROR);
    s->errors=R(VIDEO_ERRORS);s->fifo_min=R(VIDEO_FIFO_MIN);s->max_wait=R(VIDEO_MAX_WAIT);
    s->reads=R(VIDEO_READS);s->cpu_commands=R(VIDEO_CPU_COMMANDS);
    s->request_overflow=R(VIDEO_REQUEST_OVERFLOW);return AE350_VIDEO_OK;
}
