#include "internal.h"
#include "payload.h"
#include "ff.h"
#include <string.h>
#define STAGING ((uint8_t *)0x47000000u)
static const struct bios_info info={BIOS_ABI_VERSION,sizeof(struct bios_info),0x40000000,128u*1024*1024,
    BIOS_PAYLOAD_BASE,BIOS_PAYLOAD_LIMIT,{0x47e00000,0x47e40000},480,272,960,565,
    MINI_FEATURE_SD|(MINI_FEATURE_VIDEO<<1)|(MINI_FEATURE_USB<<2)|(MINI_FEATURE_ETH<<3)|(MINI_FEATURE_AUDIO<<4),60000000};
int bios_file_read(const char *path,unsigned offset,void *data,unsigned capacity) {
#if MINI_FEATURE_FILESYSTEM
    FIL f;UINT count;
    if(hal_sd_mount()!=HAL_OK || f_open(&f,path,FA_READ)!=FR_OK)return -1;
    FRESULT r=f_lseek(&f,offset);
    if(r==FR_OK)r=f_read(&f,data,capacity,&count);
    FRESULT c=f_close(&f);return r==FR_OK && c==FR_OK?(int)count:-1;
#else
    (void)path;(void)offset;(void)data;(void)capacity;return -1;
#endif
}
int bios_payload_check(const void *image,unsigned length) {
    if(length<sizeof(struct bios_image))return 0;
    const struct bios_image *h=image;
    return bios_header_valid(h,length) && h->crc==bios_crc((const uint8_t *)image+sizeof(*h),h->file_bytes);
}
int bios_self_test(void) {
    struct {struct bios_image h;uint8_t data[4];} v={{BIOS_IMAGE_MAGIC,1,BIOS_PAYLOAD_BASE,4,64,BIOS_PAYLOAD_BASE,0,0},{1,2,3,4}};
    v.h.crc=bios_crc(v.data,sizeof(v.data));v.h.header_crc=bios_crc(&v.h,sizeof(v.h)-4);
    if(!bios_payload_check(&v,sizeof(v)) || bios_payload_check(&v,sizeof(v)-1))return 0;
    v.data[0]^=1;if(bios_payload_check(&v,sizeof(v)))return 0;v.data[0]^=1;
    v.h.load=0x40800000;v.h.header_crc=bios_crc(&v.h,sizeof(v.h)-4);
    if(bios_payload_check(&v,sizeof(v)))return 0;
    return bios_call(BIOS_INFO,0x40800000,0,0,0)==-1 && bios_call(999,0,0,0,0)==-1 &&
        bios_call(BIOS_AUDIO_BEGIN,0x40800000,1024,0,0)==-1 &&
        bios_call(BIOS_AUDIO_BEGIN,BIOS_PAYLOAD_BASE+1,1024,0,0)==-1 &&
        bios_call(BIOS_AUDIO_WRITE,BIOS_PAYLOAD_BASE,1025,0,0)==-1 &&
        bios_call(BIOS_AUDIO_CONTROL,99,0,0,0)==-1;
}
int bios_payload_run(const void *image,unsigned length) {
    if(!bios_payload_check(image,length)) {bios_puts("BOOT rejected: format/range/version/CRC\r\n");return -1;}
    struct bios_image h;memcpy(&h,image,sizeof(h));
    /* BIOS owns all drivers. Quiesce output/capture DMA before handing over. */
    if(hal_audio_stop()!=HAL_OK && MINI_FEATURE_AUDIO)return -1;
    hal_audio_mute(1);hal_mic_stop();
    memcpy((void *)h.load,(const uint8_t *)image+sizeof(h),h.file_bytes);
    memset((void *)(h.load+h.file_bytes),0,h.memory_bytes-h.file_bytes);
    __asm__ volatile("fence rw,rw":::"memory");
#if MINI_CPU_DCACHE
    __asm__ volatile(".word 0x0000500f":::"memory");
#endif
    __asm__ volatile("fence.i":::"memory");
    bios_puts("BOOT payload entry=");bios_hex(h.entry);bios_puts("\r\n");
    bios_enter(h.entry,&info,BIOS_PAYLOAD_LIMIT);
    /* A returning payload must not leave DMA reading its old storage. */
    hal_audio_stop();hal_audio_mute(1);
    bios_video_mode(BIOS_TEXT);bios_puts("PAYLOAD RETURNED\r\n");return 0;
}
int bios_sd_boot(const char *path) {
#if MINI_FEATURE_FILESYSTEM
    FIL f;UINT count;unsigned n;
    if(hal_sd_mount()!=HAL_OK || f_open(&f,path,FA_READ)!=FR_OK) {bios_puts("BOOT SD unavailable/file missing\r\n");return -1;}
    n=f_size(&f);if(n<sizeof(struct bios_image) || n>BIOS_IMAGE_MAX+sizeof(struct bios_image)) {f_close(&f);return -1;}
    unsigned offset=0;FRESULT r=FR_OK;
    while(offset<n) {unsigned bytes=n-offset;if(bytes>4096)bytes=4096;
        r=f_read(&f,STAGING+offset,bytes,&count);if(r!=FR_OK || count!=bytes)break;
        offset+=count;bios_poll();
    }
    FRESULT c=f_close(&f);
    return r==FR_OK && c==FR_OK && offset==n?bios_payload_run(STAGING,n):-1;
#else
    (void)path;return -1;
#endif
}
int bios_net_boot(const uint8_t server[4],const char *path) {
    unsigned n;int r=bios_tftp_get(server,path,STAGING,BIOS_IMAGE_MAX+sizeof(struct bios_image),&n);
    if(!r)bios_puts("BOOT TFTP unavailable/timeout\r\n");
    return r?bios_payload_run(STAGING,n):-1;
}
int bios_fetch(const char *path) {
#if MINI_FEATURE_FILESYSTEM
    unsigned n;
    if(!bios_tftp_get(bios_settings.server,path,STAGING,BIOS_IMAGE_MAX+sizeof(struct bios_image),&n) || !bios_payload_check(STAGING,n)) {
        bios_puts("FETCH rejected/unavailable\r\n");return -1;
    }
    FIL f;UINT count;
    /* Explicit command creates a new file; never replaces an existing image. */
    if(hal_sd_mount()!=HAL_OK || f_open(&f,path,FA_WRITE|FA_CREATE_NEW)!=FR_OK) {
        bios_puts("FETCH SD error/file exists\r\n");return -1;
    }
    FRESULT r=FR_OK;unsigned offset=0;
    while(offset<n) {unsigned bytes=n-offset;if(bytes>4096)bytes=4096;
        r=f_write(&f,STAGING+offset,bytes,&count);if(r!=FR_OK || count!=bytes)break;
        offset+=count;bios_poll();
    }
    FRESULT c=f_close(&f);
    if(r!=FR_OK || c!=FR_OK || offset!=n) {f_unlink(path);bios_puts("FETCH SD write failed\r\n");return -1;}
    bios_puts("FETCH SAVED ");bios_puts(path);bios_puts("\r\n");return 0;
#else
    (void)path;return -1;
#endif
}
static int pointer_ok(uint32_t p,unsigned n) {return p>=BIOS_PAYLOAD_BASE && p<BIOS_PAYLOAD_LIMIT && n<=BIOS_PAYLOAD_LIMIT-p;}
int bios_exception_hook(hal_trap_frame_t *f) {
    if(f->cause!=11 || f->gpr[17]!=BIOS_ECALL_MAGIC)return 0;
    uint32_t *a=&f->gpr[10];int result=-1;
    switch(f->gpr[16]) {
    case BIOS_INFO:if(pointer_ok(a[0],sizeof(info))) {memcpy((void *)a[0],&info,sizeof(info));result=0;}break;
    case BIOS_WRITE:if(a[1]<=4096 && pointer_ok(a[0],a[1])) {for(unsigned i=0;i<a[1];++i)bios_putc(((char *)a[0])[i]);result=a[1];}break;
    case BIOS_GETC:result=bios_getc();break;
    case BIOS_TIME:result=hal_time_ms();break;
    case BIOS_POLL:bios_poll();result=0;break;
    case BIOS_VIDEO_MODE:result=bios_video_mode(a[0]);break;
    case BIOS_VIDEO_PRESENT:result=a[0]<2 && hal_video_present(a[0])==HAL_OK?0:-1;break;
    case BIOS_SD_READ:case BIOS_SD_WRITE:
        if(a[2] && a[2]<=8 && pointer_ok(a[1],a[2]*512))result=(f->gpr[16]==BIOS_SD_READ?hal_sd_read(a[0],(void *)a[1],a[2]):hal_sd_write(a[0],(void *)a[1],a[2]))==HAL_OK?0:-1;
        break;
    case BIOS_FILE_READ:
        if(pointer_ok(a[0],64) && pointer_ok(a[2],a[3]) && a[3]<=4096) {
            unsigned n=0;while(n<64 && ((char *)a[0])[n])++n;
            if(n<64)result=bios_file_read((char *)a[0],a[1],(void *)a[2],a[3]);
        }break;
    case BIOS_IO_READ:if(pointer_ok(a[0],sizeof(struct bios_io))) {struct bios_io v={hal_buttons_read(),hal_switches_read()};memcpy((void *)a[0],&v,sizeof(v));result=0;}break;
    case BIOS_LEDS:hal_leds_set(a[0]);result=0;break;
    case BIOS_FLASH_READ:
        if(a[2]<=4096 && pointer_ok(a[1],a[2]))result=hal_flash_read(a[0],(void *)a[1],a[2])==HAL_OK?0:-1;
        break;
    case BIOS_RGB:result=hal_ws2812_set(a[0]>>16,a[0]>>8,a[0])==HAL_OK?0:-1;break;
    case BIOS_MOUSE:if(pointer_ok(a[0],sizeof(struct bios_mouse)))result=bios_mouse_take((void *)a[0]);break;
    case BIOS_AUDIO_BEGIN:
        if(MINI_FEATURE_AUDIO && !(a[0]&3u) && a[1] && a[1]<=65535 && pointer_ok(a[0],a[1]*4))
            result=hal_audio_ring_begin((void *)a[0],a[1])==HAL_OK?0:-1;
        break;
    case BIOS_AUDIO_WRITE:
        if(MINI_FEATURE_AUDIO && !(a[0]&3u) && a[1]<=1024 && pointer_ok(a[0],a[1]*4)) {
            unsigned written=0;hal_result_t r=hal_audio_ring_write((void *)a[0],a[1],&written);
            if(r==HAL_OK || r==HAL_BUSY)result=written;
        }break;
    case BIOS_AUDIO_CONTROL:
        if(!MINI_FEATURE_AUDIO)break;
        switch(a[0]) {
        case BIOS_AUDIO_STOP:result=hal_audio_stop()==HAL_OK?0:-1;break;
        case BIOS_AUDIO_PLAY:result=hal_audio_start()==HAL_OK?0:-1;break;
        case BIOS_AUDIO_PAUSE:hal_audio_pause();result=0;break;
        case BIOS_AUDIO_MUTE:hal_audio_mute(1);result=0;break;
        case BIOS_AUDIO_UNMUTE:hal_audio_mute(0);result=0;break;
        }break;
    case BIOS_AUDIO_INFO:
        if(MINI_FEATURE_AUDIO && pointer_ok(a[0],sizeof(struct bios_audio))) {
            hal_audio_info_t v;hal_audio_get_info(&v);
            struct bios_audio out={v.sample_rate,v.level,v.played,v.fetched,v.underruns,v.overruns,v.errors};
            memcpy((void *)a[0],&out,sizeof(out));result=0;
        }break;
    case BIOS_REBOOT:hal_reboot();
    }
    f->gpr[10]=result;f->pc+=4;return 1;
}
