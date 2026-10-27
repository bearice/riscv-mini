#include "ae350_bios.h"
#include "ae350_address_map.h"
#include "ae350_tty.h"
#include "ae350_audio.h"
#include "board_management.h"
#include "board_io.h"
#include "ae350_sd.h"
#include "ff.h"
#include <string.h>
#define W(a) (*(volatile uint32_t *)(uintptr_t)(a))
static struct ae350_tty tty;
static unsigned video_ready,graphics;
static uint32_t last_flush;
static uint32_t *audio_ring;
static unsigned ring_size,ring_head,ring_tail,ring_count,audio_active,audio_error;
static uint32_t audio_fetched;
static _Alignas(32) uint32_t audio_stage[8192];
static void audio_feed(void) {
    if(!audio_ring || audio_error)return;
    if(audio_active) {
        int rc=audio_tx_dma_done();
        if(rc==AE350_DMA_ERR_BUSY)return;
        if(rc) {audio_error=(unsigned)rc;return;}
        audio_active=0;
    }
    if(!ring_count)return;
    unsigned n=ring_count>8192?8192:ring_count;
    for(unsigned i=0;i<n;i++)audio_stage[i]=audio_ring[(ring_head+i)%ring_size];
    if(n<8) {
        unsigned written=0;int rc=audio_tx_write(audio_stage,n,&written);
        if(rc && rc!=AE350_DMA_ERR_BUSY)audio_error=(unsigned)rc;
        ring_head=(ring_head+written)%ring_size;ring_count-=written;audio_fetched+=written;
        return;
    }
    n&=~7u; /* Retain the tail until the next refill; never add PCM samples. */
    int rc=audio_tx_dma_start(0,audio_stage,n);
    if(rc) {audio_error=(unsigned)rc;return;}
    audio_active=1;ring_head=(ring_head+n)%ring_size;ring_count-=n;audio_fetched+=n;
}
struct bios_info ae350_bios_info={BIOS_ABI_VERSION,sizeof(struct bios_info),0,AE350_DDR_SIZE,
    BIOS_PAYLOAD_BASE,BIOS_PAYLOAD_LIMIT,{0x08200000u,0x08300000u},640,480,1280,565,0,AE350_CPU_HZ};
uint32_t bios_time_ms(void) {
    uint32_t hi,lo,again;
    do {__asm__ volatile("rdcycleh %0\nrdcycle %1\nrdcycleh %2":"=r"(hi),"=r"(lo),"=r"(again));}while(hi!=again);
    return (uint32_t)((((uint64_t)hi<<32)|lo)/(AE350_CPU_HZ/1000u));
}
int bios_getc(void) {return W(0xf0300034u)&1u?(int)(W(0xf0300020u)&255u):-1;}
void bios_putc(char c) {
    while(!(W(0xf0300034u)&0x20u)) {}
    W(0xf0300020u)=(uint8_t)c;
    if(video_ready && !graphics)ae350_tty_putc(&tty,c);
}
void bios_puts(const char *s) {while(*s)bios_putc(*s++);}
void bios_poll(void) {
    audio_feed();
    uint32_t now=bios_time_ms();
    if(video_ready && !graphics && now-last_flush>=50) {
        if(ae350_tty_flush(&tty))video_ready=0;
        last_flush=now;
    }
}
static int pointer(uint32_t p,unsigned bytes) {
    extern char __text_start[],__bss_end[];
    if(bytes>0x1000000u)return 0;
    return (p>=(uintptr_t)__text_start && p<=(uintptr_t)__bss_end && bytes<=(uintptr_t)__bss_end-p) ||
        (p>=AE350_PAYLOAD_STACK_BOTTOM && p<=AE350_PAYLOAD_STACK_TOP && bytes<=AE350_PAYLOAD_STACK_TOP-p) ||
        (p>=BIOS_PAYLOAD_BASE && p<=BIOS_PAYLOAD_LIMIT && bytes<=BIOS_PAYLOAD_LIMIT-p);
}
static int path(uint32_t p) {
    for(unsigned i=0;i<256;i++) {if(!pointer(p+i,1))return 0;if(!((char *)(uintptr_t)p)[i])return 1;}
    return 0;
}
/* Saved caller registers: ra,t0..t6,a0..a7. Runtime preserves all other GPRs.
 * Pointer checks protect device ownership, not a privilege/security boundary. */
void ecall_dispatch(uint32_t *frame) {
    uint32_t *a=frame+8;int result=-1;
    if(a[7]!=BIOS_ECALL_MAGIC) {a[0]=(uint32_t)-1;return;}
    switch(a[6]) {
    case BIOS_INFO:
        if(pointer(a[0],sizeof(ae350_bios_info))) {memcpy((void *)a[0],&ae350_bios_info,sizeof(ae350_bios_info));result=0;}break;
    case BIOS_WRITE:
        if(a[1]<=4096 && pointer(a[0],a[1])) {for(unsigned i=0;i<a[1];i++)bios_putc(((char *)a[0])[i]);result=(int)a[1];}break;
    case BIOS_GETC:result=bios_getc();break;
    case BIOS_TIME:result=(int)bios_time_ms();break;
    case BIOS_POLL:bios_poll();result=0;break;
    case BIOS_FILE_READ:
        if(path(a[0]) && a[3]<=4096 && pointer(a[2],a[3]))result=bios_file_read((char *)a[0],a[1],(void *)a[2],a[3]);
        break;
    case BIOS_FILE_SIZE:if(path(a[0]))result=bios_file_size((char *)a[0]);break;
    case BIOS_FS_OPEN:if(path(a[0]))result=bios_fs_open((char *)a[0],a[1]);break;
    case BIOS_FS_CLOSE:result=bios_fs_close((int)a[0]);break;
    case BIOS_FS_READ:if(a[2]<=4096 && pointer(a[1],a[2]))result=bios_fs_read((int)a[0],(void *)a[1],a[2]);break;
    case BIOS_FS_WRITE:if(a[2]<=4096 && pointer(a[1],a[2]))result=bios_fs_write((int)a[0],(void *)a[1],a[2]);break;
    case BIOS_FS_SEEK:result=bios_fs_seek((int)a[0],(int)a[1],(int)a[2]);break;
    case BIOS_FS_STAT:if(path(a[0]) && pointer(a[1],sizeof(struct bios_fs_stat)))result=bios_fs_stat((char *)a[0],(void *)a[1]);break;
    case BIOS_FS_MKDIR:if(path(a[0]) && !bios_mount())result=f_mkdir((char *)a[0])==FR_OK?0:-1;break;
    case BIOS_FS_UNLINK:if(path(a[0]) && !bios_mount())result=f_unlink((char *)a[0])==FR_OK?0:-1;break;
    case BIOS_FS_RENAME:if(path(a[0]) && path(a[1]) && !bios_mount())result=f_rename((char *)a[0],(char *)a[1])==FR_OK?0:-1;break;
    case BIOS_VIDEO_MODE:
        if(video_ready && a[0]<=1) {
            if(a[0]==BIOS_GRAPHICS) {result=ae350_video_stop()? -1:0;if(!result)graphics=1;}
            else {graphics=0;tty.initialized[0]=tty.initialized[1]=0;tty.dirty=1;result=ae350_tty_flush(&tty)?-1:0;}
        }break;
    case BIOS_VIDEO_PRESENT:
        if(graphics && a[0]<2) {
            struct ae350_video_status status;
            if(ae350_video_status(&status))break;
            unsigned back=status.enabled?(status.active^1u):0u;
            uint16_t *destination=ae350_video_frame(back);
            if(!destination)break;
            memcpy(destination,(void *)ae350_bios_info.framebuffer[a[0]],480u*1280u);
            result=ae350_video_present(back)?-1:0;
        }break;
    case BIOS_IO_READ:
        if(pointer(a[0],sizeof(struct bios_io))) {
            struct bios_io value={0,0};result=0;
            for(unsigned i=0;i<(unsigned)board_button_count();i++) {
                unsigned pressed;if(board_button_get(i,&pressed)) {result=-1;break;}
                value.buttons|=pressed<<i;
            }
            if(!result)memcpy((void *)a[0],&value,sizeof(value));
        }break;
    case BIOS_LEDS:
        result=0;
        for(unsigned i=0;i<(unsigned)board_led_count();i++)if(board_led_set(i,(a[0]>>i)&1u))result=-1;
        break;
    case BIOS_FLASH_READ:if(a[2]<=4096 && pointer(a[1],a[2]))result=board_flash_read(a[0],(void *)a[1],a[2])?-1:0;break;
    case BIOS_RGB:result=board_ws2812_set(a[0]>>16,a[0]>>8,a[0])?-1:0;break;
    case BIOS_AUDIO_BEGIN:
        if(!(a[0]&3u) && a[1] && a[1]<=65535 && pointer(a[0],a[1]*4u)) {
            if(!audio_tx_init()) {
                audio_ring=(uint32_t *)a[0];ring_size=a[1];
                ring_head=ring_tail=ring_count=audio_active=audio_error=audio_fetched=0;result=0;
            }
        }
        break;
    case BIOS_AUDIO_WRITE:
        if(audio_ring && !audio_error && !(a[0]&3u) && a[1]<=1024 && pointer(a[0],a[1]*4u)) {
            audio_feed();
            unsigned n=a[1];if(n>ring_size-ring_count)n=ring_size-ring_count;
            const uint32_t *source=(const uint32_t *)a[0];
            for(unsigned i=0;i<n;i++)audio_ring[(ring_tail+i)%ring_size]=source[i];
            ring_tail=(ring_tail+n)%ring_size;ring_count+=n;audio_feed();result=(int)n;
        }break;
    case BIOS_AUDIO_CONTROL:
        if(a[0]==BIOS_AUDIO_STOP) {
            result=audio_tx_stop()?-1:0;
            if(!result) {audio_ring=0;audio_active=ring_count=audio_error=audio_fetched=0;}
        }
        else if(a[0]==BIOS_AUDIO_PLAY)result=audio_tx_start(0)?-1:0;
        else if(a[0]==BIOS_AUDIO_PAUSE)result=audio_tx_pause()?-1:0;
        else if(a[0]==BIOS_AUDIO_MUTE || a[0]==BIOS_AUDIO_UNMUTE)result=audio_tx_mute(a[0]==BIOS_AUDIO_MUTE)?-1:0;
        break;
    case BIOS_AUDIO_INFO:
        if(pointer(a[0],sizeof(struct bios_audio))) {
            struct bios_audio v={audio_tx_info(AUDIO_RATE),audio_tx_info(AUDIO_LEVEL),audio_tx_info(AUDIO_PLAYED),
                audio_fetched,audio_tx_info(AUDIO_UNDERRUN),0,audio_error|audio_tx_info(0x1c)};
            memcpy((void *)a[0],&v,sizeof(v));result=0;
        }break;
    }
    a[0]=(uint32_t)result;
}
void bios_runtime_init(void) {
    __asm__ volatile("csrw 0x7cc,%0\ncsrs 0x7ca,%1"::"r"(23),"r"(3):"memory");
    struct board_flash_info flash;
    board_flash_init(&flash);
    if(!bios_mount())ae350_bios_info.features|=1u;
    video_ready=!ae350_video_init((uint16_t *)0x08000000u,(uint16_t *)0x08100000u,1280);
    if(video_ready) {ae350_bios_info.features|=2u|BIOS_FEATURE_VIDEO_SHADOW;ae350_tty_init(&tty);}
    if(!audio_tx_init())ae350_bios_info.features|=1u<<4;
}
