#include "internal.h"
#include "payload.h"
#include "ff.h"
#include <generated/csr.h>
#include <string.h>
#define STAGING ((uint8_t *)0x07000000u)
extern void bios_os_enter(uint32_t entry) __attribute__((noreturn));
static struct bios_info info={BIOS_ABI_VERSION,sizeof(struct bios_info),MINI_RAM_BASE,MINI_RAM_SIZE,
    BIOS_PAYLOAD_BASE,BIOS_PAYLOAD_LIMIT,{0,0},480,272,960,565,
    MINI_FEATURE_SD|(MINI_FEATURE_VIDEO<<1)|(MINI_FEATURE_USB<<2)|(MINI_FEATURE_ETH<<3)|(MINI_FEATURE_AUDIO<<4)|(MINI_FEATURE_VIDEO<<5),60000000};
/* ls 的目录参数版：NULL/空串按根目录处理，无参输出与旧 bios_sd_list 逐字节一致。
 * 只列一层、不递归——载荷和脚本都靠这个输出挑文件，深度遍历留给上层。 */
void bios_sd_list_dir(const char *path) {
#if MINI_FEATURE_FILESYSTEM
    if(!path || !path[0])path="/";
    if(strlen(path)>=64) {bios_puts("LS: filename too long\r\n");return;}
    if(hal_sd_mount()!=HAL_OK) {bios_puts("LS: SD unavailable\r\n");return;}
    DIR directory;FILINFO entry;
    FRESULT r=f_opendir(&directory,path);
    if(r!=FR_OK) {bios_puts("LS: open failed FatFs=");bios_decimal(r);bios_puts("\r\n");return;}
    unsigned count=0;
    bios_puts("SD ");bios_puts(path);bios_puts("\r\n");
    for(;;) {
        r=f_readdir(&directory,&entry);
        if(r!=FR_OK || !entry.fname[0])break;
        bios_puts(entry.fname);if(entry.fattrib&AM_DIR)bios_putc('/');bios_puts("\r\n");
        ++count;bios_poll();
    }
    FRESULT closed=f_closedir(&directory);
    if(r==FR_OK)r=closed;
    if(r!=FR_OK) {bios_puts("LS: read/close failed FatFs=");bios_decimal(r);bios_puts("\r\n");}
    else {bios_puts("LS: ");bios_decimal(count);bios_puts(" entries\r\n");}
#else
    (void)path;
    bios_puts("LS: filesystem disabled\r\n");
#endif
}
void bios_sd_list(void) {bios_sd_list_dir(0);}
/* 删除 SD 上的一个文件。fetch/fetchraw 故意不覆盖已存在的文件（见
 * staging_write_sd 的显式创建），所以换一份 WAD 或镜像要先删掉旧的；
 * f_unlink 不区分"不存在"和"只读"，FatFs 错误码原样打出来。 */
void bios_sd_remove(const char *path) {
#if MINI_FEATURE_FILESYSTEM
    if(!path[0] || strlen(path)>=64) {bios_puts("RM: filename too long\r\n");return;}
    FRESULT r=f_unlink(path);
    if(r==FR_OK) {bios_puts("RM: deleted ");bios_puts(path);bios_puts("\r\n");}
    else {bios_puts("RM: failed FatFs=");bios_decimal(r);bios_puts("\r\n");}
#else
    (void)path;
    bios_puts("RM: filesystem disabled\r\n");
#endif
}
/* ---------------------------------------------------------- SD 文件控制台命令
 * cat/size/mkdir/mv 都是文件系统层之上的薄壳：路径上限与 rm 一致（<64，
 * 和 ecall 分发同规矩），mkdir/mv 直接调 f_mkdir/f_rename 而不是
 * bios_fs_*，因为控制台要把 FRESULT 原样打出来——bios_fs_mkdir 把
 * FR_EXIST 折叠成成功，控制台恰恰需要区分"目录已存在"。 */
/* cat 按 256 B 分块：UART 115200 下这个尺寸不会淹没接收端，
 * 每块之间 bios_poll() 让键盘/USB 在长文件期间保持响应。 */
void bios_sd_cat(const char *path,unsigned max) {
#if MINI_FEATURE_FILESYSTEM
    if(!path[0] || strlen(path)>=64) {bios_puts("CAT: not found or unreadable\r\n");return;}
    if(!max)max=4096;
    int handle=bios_fs_open(path,BIOS_FS_O_READ);
    if(handle<0) {bios_puts("CAT: not found or unreadable\r\n");return;}
    unsigned char buf[256];unsigned shown=0,more=0;
    while(shown<max) {
        unsigned want=max-shown;if(want>sizeof(buf))want=sizeof(buf);
        int got=bios_fs_read(handle,buf,want);
        if(got<=0)break;
        for(int i=0;i<got;++i)bios_putc((char)buf[i]);
        shown+=(unsigned)got;bios_poll();
    }
    if(shown>=max)more=bios_fs_read(handle,buf,1)>0;  /* 恰好读满不算截断，探一个字节才算 */
    bios_fs_close(handle);
    bios_puts("CAT: ");bios_decimal(shown);bios_puts(" bytes");
    if(more)bios_puts(" (truncated)");
    bios_puts("\r\n");
#else
    (void)path;(void)max;
    bios_puts("CAT: filesystem disabled\r\n");
#endif
}
void bios_sd_stat(const char *path) {
#if MINI_FEATURE_FILESYSTEM
    struct bios_fs_stat st;
    if(!path[0] || strlen(path)>=64 || bios_fs_stat(path,&st)!=0) {bios_puts("SIZE: not found\r\n");return;}
    bios_puts("SIZE: ");bios_puts(path);bios_puts(" ");bios_decimal(st.size);bios_puts(" bytes");
    if(st.attrib&AM_DIR)bios_puts(" DIR");
    if(st.attrib&AM_RDO)bios_puts(" RO");
    bios_puts("\r\n");
#else
    (void)path;
    bios_puts("SIZE: filesystem disabled\r\n");
#endif
}
void bios_sd_mkdir(const char *path) {
#if MINI_FEATURE_FILESYSTEM
    if(!path[0] || strlen(path)>=64) {bios_puts("MKDIR: filename too long\r\n");return;}
    FRESULT r=f_mkdir(path);
    if(r==FR_OK) {bios_puts("MKDIR: created ");bios_puts(path);bios_puts("\r\n");}
    else {bios_puts("MKDIR: failed FatFs=");bios_decimal(r);bios_puts("\r\n");}
#else
    (void)path;
    bios_puts("MKDIR: filesystem disabled\r\n");
#endif
}
/* f_rename 不覆盖已存在的目标，撞名会报 FR_EXIST 而不是静默吃掉旧文件。 */
void bios_sd_move(const char *from,const char *to) {
#if MINI_FEATURE_FILESYSTEM
    if(!from[0] || strlen(from)>=64 || !to[0] || strlen(to)>=64) {bios_puts("MV: filename too long\r\n");return;}
    FRESULT r=f_rename(from,to);
    if(r==FR_OK) {bios_puts("MV: ");bios_puts(from);bios_puts(" -> ");bios_puts(to);bios_puts("\r\n");}
    else {bios_puts("MV: failed FatFs=");bios_decimal(r);bios_puts("\r\n");}
#else
    (void)from;(void)to;
    bios_puts("MV: filesystem disabled\r\n");
#endif
}
/* ------------------------------------------------------------- file system
 * One layer over FatFs used by every BIOS file user: the payload ecall
 * services, the console commands (fetch/fetchraw/boot sd/settings) and the
 * legacy FILE_READ/FILE_SIZE pair.  Handles are indices into this table; a
 * handle owned by one payload is just an integer, so callers pass back what
 * they were given.  sd_mount() keeps the volume mounted, so an open handle
 * stays valid across the whole run. */
#if MINI_FEATURE_FILESYSTEM
#define BIOS_FS_HANDLES 6
static struct {FIL fil;int used,writable;} fs_handles[BIOS_FS_HANDLES];

static int fs_handle_ok(int handle) {
    return handle>=0 && handle<BIOS_FS_HANDLES && fs_handles[handle].used;
}
int bios_fs_open(const char *path,unsigned flags) {
    if(hal_sd_mount()!=HAL_OK)return -1;
    BYTE mode=0;
    if(flags&BIOS_FS_O_WRITE)mode|=FA_WRITE;
    if(flags&BIOS_FS_O_CREATE)mode|=(flags&BIOS_FS_O_TRUNC)?FA_CREATE_ALWAYS:FA_CREATE_NEW;
    else if(flags&BIOS_FS_O_TRUNC)mode|=FA_CREATE_ALWAYS;
    if(flags&BIOS_FS_O_APPEND)mode|=FA_OPEN_APPEND;
    if(!(flags&(BIOS_FS_O_WRITE|BIOS_FS_O_CREATE|BIOS_FS_O_TRUNC|BIOS_FS_O_APPEND)))mode=FA_READ;
    for(int i=0;i<BIOS_FS_HANDLES;++i) {
        if(fs_handles[i].used)continue;
        if(f_open(&fs_handles[i].fil,path,mode)!=FR_OK)return -1;
        fs_handles[i].used=1;fs_handles[i].writable=!!(flags&BIOS_FS_O_WRITE);
        return i;
    }
    return -1; /* handle table full */
}
int bios_fs_close(int handle) {
    if(!fs_handle_ok(handle))return -1;
    FRESULT r=f_close(&fs_handles[handle].fil);
    fs_handles[handle].used=0;
    return r==FR_OK?0:-1;
}
int bios_fs_read(int handle,void *data,unsigned length) {
    if(!fs_handle_ok(handle))return -1;
    UINT got=0;FRESULT r=f_read(&fs_handles[handle].fil,data,length,&got);
    return r==FR_OK?(int)got:-1;
}
int bios_fs_write(int handle,const void *data,unsigned length) {
    if(!fs_handle_ok(handle) || !fs_handles[handle].writable)return -1;
    UINT put=0;FRESULT r=f_write(&fs_handles[handle].fil,data,length,&put);
    return r==FR_OK?(int)put:-1;
}
int bios_fs_seek(int handle,int offset,int whence) {
    if(!fs_handle_ok(handle))return -1;
    FIL *f=&fs_handles[handle].fil;
    FSIZE_t base;
    switch(whence) {
    case BIOS_FS_SET:base=0;break;
    case BIOS_FS_CUR:base=f_tell(f);break;
    case BIOS_FS_END:base=f_size(f);break;
    default:return -1;
    }
    FSIZE_t target=offset<0?(base<(FSIZE_t)(-offset)?0:base-(FSIZE_t)(-offset)):base+(FSIZE_t)offset;
    if(f_lseek(f,target)!=FR_OK)return -1;
    return (int)f_tell(f);
}
int bios_fs_stat(const char *path,struct bios_fs_stat *out) {
    FILINFO info;
    if(hal_sd_mount()!=HAL_OK)return -1;
    if(f_stat(path,&info)!=FR_OK)return -1;
    out->size=(uint32_t)info.fsize;out->attrib=info.fattrib;
    return 0;
}
int bios_fs_mkdir(const char *path) {
    if(hal_sd_mount()!=HAL_OK)return -1;
    FRESULT r=f_mkdir(path);
    return (r==FR_OK || r==FR_EXIST)?0:-1;
}
int bios_fs_unlink(const char *path) {
    if(hal_sd_mount()!=HAL_OK)return -1;
    return f_unlink(path)==FR_OK?0:-1;
}
int bios_fs_rename(const char *from,const char *to) {
    if(hal_sd_mount()!=HAL_OK)return -1;
    return f_rename(from,to)==FR_OK?0:-1;
}
#else
/* Keep the BIOS ecall ABI linkable when the filesystem feature is disabled. */
int bios_fs_open(const char *path,unsigned flags) {(void)path;(void)flags;return -1;}
int bios_fs_close(int handle) {(void)handle;return -1;}
int bios_fs_read(int handle,void *data,unsigned length) {(void)handle;(void)data;(void)length;return -1;}
int bios_fs_write(int handle,const void *data,unsigned length) {(void)handle;(void)data;(void)length;return -1;}
int bios_fs_seek(int handle,int offset,int whence) {(void)handle;(void)offset;(void)whence;return -1;}
int bios_fs_stat(const char *path,struct bios_fs_stat *out) {(void)path;(void)out;return -1;}
int bios_fs_mkdir(const char *path) {(void)path;return -1;}
int bios_fs_unlink(const char *path) {(void)path;return -1;}
int bios_fs_rename(const char *from,const char *to) {(void)from;(void)to;return -1;}
#endif

/* Legacy services, now thin clients of the layer above. */
int bios_file_read(const char *path,unsigned offset,void *data,unsigned capacity) {
#if MINI_FEATURE_FILESYSTEM
    int handle=bios_fs_open(path,BIOS_FS_O_READ);
    if(handle<0)return -1;
    int result=-1;
    if(bios_fs_seek(handle,(int)offset,BIOS_FS_SET)>=0)result=bios_fs_read(handle,data,capacity);
    bios_fs_close(handle);
    return result;
#else
    (void)path;(void)offset;(void)data;(void)capacity;return -1;
#endif
}
int bios_file_size(const char *path) {
#if MINI_FEATURE_FILESYSTEM
    struct bios_fs_stat info;
    if(bios_fs_stat(path,&info)!=0)return -1;
    return (int)info.size;
#else
    (void)path;return -1;
#endif
}
int bios_payload_check(const void *image,unsigned length) {
    if(length<sizeof(struct bios_image))return 0;
    const struct bios_image *h=image;
    if(!bios_header_valid(h,length))return 0;
    uint32_t crc=~0u;const uint8_t *data=(const uint8_t *)image+sizeof(*h);
    for(unsigned offset=0;offset<h->file_bytes;) {
        unsigned bytes=h->file_bytes-offset;if(bytes>65536)bytes=65536;
        crc=bios_crc_update(crc,data+offset,bytes);offset+=bytes;
        bios_poll();
    }
    return h->crc==~crc;
}
int bios_self_test(void) {
    struct {struct bios_image h;uint8_t data[4];} v={{BIOS_IMAGE_MAGIC,1,BIOS_PAYLOAD_BASE,4,64,BIOS_PAYLOAD_BASE,0,0},{1,2,3,4}};
    v.h.crc=bios_crc(v.data,sizeof(v.data));v.h.header_crc=bios_crc(&v.h,sizeof(v.h)-4);
    if(!bios_payload_check(&v,sizeof(v)) || bios_payload_check(&v,sizeof(v)-1))return 0;
    v.data[0]^=1;if(bios_payload_check(&v,sizeof(v)))return 0;v.data[0]^=1;
    v.h.load=0x00800000;v.h.header_crc=bios_crc(&v.h,sizeof(v.h)-4);
    if(bios_payload_check(&v,sizeof(v)))return 0;
    return bios_call(BIOS_INFO,0x00800000,0,0,0)==-1 && bios_call(999,0,0,0,0)==-1 &&
        bios_call(BIOS_AUDIO_BEGIN,0x00800000,1024,0,0)==-1 &&
        bios_call(BIOS_AUDIO_BEGIN,BIOS_PAYLOAD_BASE+1,1024,0,0)==-1 &&
        bios_call(BIOS_AUDIO_WRITE,BIOS_PAYLOAD_BASE,1025,0,0)==-1 &&
        bios_call(BIOS_AUDIO_CONTROL,99,0,0,0)==-1;
}
int bios_payload_run(const void *image,unsigned length) {
    info.framebuffer[0]=(uintptr_t)hal_video_frame(0);info.framebuffer[1]=(uintptr_t)hal_video_frame(1);
    unsigned start=hal_time_ms();
    bios_puts("BOOT checking image...\r\n");
    if(!bios_payload_check(image,length)) {bios_puts("BOOT rejected: format/range/version/CRC\r\n");return -1;}
    bios_boot_timing("CRC",start);
    struct bios_image h;memcpy(&h,image,sizeof(h));
    if(h.magic==BIOS_OS_IMAGE_MAGIC && !MINI_FEATURE_MMU)return -1;
    /* BIOS owns all drivers. Quiesce output/capture DMA before handing over. */
    if(hal_audio_stop()!=HAL_OK && MINI_FEATURE_AUDIO)return -1;
    hal_audio_mute(1);hal_mic_stop();
    start=hal_time_ms();
    memcpy((void *)h.load,(const uint8_t *)image+sizeof(h),h.file_bytes);
    bios_boot_timing("copy",start);start=hal_time_ms();
    memset((void *)(h.load+h.file_bytes),0,h.memory_bytes-h.file_bytes);
    __asm__ volatile("fence rw,rw":::"memory");
#if MINI_CPU_DCACHE
    __asm__ volatile(".word 0x0000500f":::"memory");
#endif
    __asm__ volatile("fence.i":::"memory");
    bios_boot_timing("zero/cache sync",start);
    bios_puts("BOOT payload entry=");bios_hex(h.entry);bios_puts("\r\n");
    if(h.magic==BIOS_OS_IMAGE_MAGIC) {
        hal_usb_stop();hal_eth_stop();
        if(hal_video_stop()!=HAL_OK && MINI_FEATURE_VIDEO)return -1;
        timer1_en_write(0);timer1_ev_enable_write(0);uart_ev_enable_write(0);
        bios_puts("BOOT OpenSBI (one-way)\r\n");
        bios_os_enter(h.entry);
    }
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
    bios_load_begin("SD");
    unsigned offset=0;FRESULT r=FR_OK;
    while(offset<n) {unsigned bytes=n-offset;if(bytes>4096)bytes=4096;
        r=f_read(&f,STAGING+offset,bytes,&count);if(r!=FR_OK || count!=bytes)break;
        offset+=count;bios_load_progress(offset);bios_poll();
    }
    FRESULT c=f_close(&f);
    bios_load_end(offset,r==FR_OK && c==FR_OK && offset==n);
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
#if MINI_FEATURE_FILESYSTEM
/* 把 STAGING 中的 n 字节写成 SD 上的新文件：显式创建、不覆盖已存在文件、
 * 失败时删除半成品。经文件系统层，FETCH 与 FETCHRAW 共用。 */
static int staging_write_sd(const char *path,unsigned n,const char *tag) {
    int handle=bios_fs_open(path,BIOS_FS_O_WRITE|BIOS_FS_O_CREATE);
    if(handle<0) {bios_puts(tag);bios_puts(" SD error/file exists\r\n");return -1;}
    unsigned offset=0;int ok=1;
    while(offset<n) {
        unsigned bytes=n-offset;if(bytes>4096)bytes=4096;
        int put=bios_fs_write(handle,STAGING+offset,bytes);
        if(put!=(int)bytes) {ok=0;break;}
        offset+=(unsigned)put;bios_poll();
    }
    if(bios_fs_close(handle)!=0)ok=0;
    if(!ok) {f_unlink(path);bios_puts(tag);bios_puts(" SD write failed\r\n");return -1;}
    bios_puts(tag);bios_puts(" SAVED ");bios_puts(path);bios_puts("\r\n");return 0;
}
#endif
int bios_fetch(const char *path) {
#if MINI_FEATURE_FILESYSTEM
    unsigned n;
    if(!bios_tftp_get(bios_settings.server,path,STAGING,BIOS_IMAGE_MAX+sizeof(struct bios_image),&n) || !bios_payload_check(STAGING,n)) {
        bios_puts("FETCH rejected/unavailable\r\n");return -1;
    }
    return staging_write_sd(path,n,"FETCH");
#else
    (void)path;return -1;
#endif
}
/* FETCHRAW：原样搬运任意数据文件（不做 RPB 校验，上限 BIOS_FILE_FETCH_MAX）。
 * 载荷镜像用 FETCH；数据文件（例如 DOOM 的 IWAD）用 FETCHRAW。 */
int bios_fetch_raw(const char *path) {
#if MINI_FEATURE_FILESYSTEM
    unsigned n;
    bios_puts("FETCHRAW ");bios_puts(path);bios_puts("\r\n");
    if(!bios_tftp_get(bios_settings.server,path,STAGING,BIOS_FILE_FETCH_MAX,&n)) {
        bios_puts("FETCHRAW rejected/unavailable\r\n");return -1;
    }
    return staging_write_sd(path,n,"FETCHRAW");
#else
    (void)path;return -1;
#endif
}
static int pointer_ok(uint32_t p,unsigned n) {return p>=BIOS_PAYLOAD_BASE && p<BIOS_PAYLOAD_LIMIT && n<=BIOS_PAYLOAD_LIMIT-p;}
int bios_exception_hook(hal_trap_frame_t *f) {
    if(f->cause!=11 || f->gpr[17]!=BIOS_ECALL_MAGIC)return 0;
    uint32_t *a=&f->gpr[10];int result=-1;
    switch(f->gpr[16]) {
    case BIOS_INFO:if(pointer_ok(a[0],sizeof(info))) {info.format=hal_video_format();info.stride=480*(info.format==8?1:2);info.framebuffer[0]=(uintptr_t)hal_video_frame(0);info.framebuffer[1]=(uintptr_t)hal_video_frame(1);memcpy((void *)a[0],&info,sizeof(info));result=0;}break;
    case BIOS_WRITE:if(a[1]<=4096 && pointer_ok(a[0],a[1])) {for(unsigned i=0;i<a[1];++i)bios_putc(((char *)a[0])[i]);result=a[1];}break;
    case BIOS_GETC:result=bios_getc();break;
    case BIOS_TIME:result=hal_time_ms();break;
    case BIOS_POLL:bios_poll();result=0;break;
    case BIOS_VIDEO_MODE:result=bios_video_mode(a[0]);break;
    case BIOS_VIDEO_PALETTE:
    case BIOS_VIDEO_PALETTE_READ:
        if(a[2]==256 && !(a[0]&1u) && pointer_ok(a[0],512))result=hal_video_palette((uint16_t *)a[0],a[1],a[2],f->gpr[16]==BIOS_VIDEO_PALETTE_READ)==HAL_OK?0:-1;
        break;
    case BIOS_VIDEO_PRESENT_INDEX8:result=hal_video_present_indexed(a[0],a[1])==HAL_OK?0:-1;break;
    case BIOS_VIDEO_WINDOW:result=hal_video_window(a[0],a[1],a[2],a[3])==HAL_OK?0:-1;break;
    case BIOS_VIDEO_PRESENT:result=a[0]<2 && hal_video_present(a[0])==HAL_OK?0:-1;break;
    case BIOS_SD_READ:case BIOS_SD_WRITE:
        if(a[2] && a[2]<=8 && pointer_ok(a[1],a[2]*512))result=(f->gpr[16]==BIOS_SD_READ?hal_sd_read(a[0],(void *)a[1],a[2]):hal_sd_write(a[0],(void *)a[1],a[2]))==HAL_OK?0:-1;
        break;
    case BIOS_FILE_READ:
        if(pointer_ok(a[0],64) && pointer_ok(a[2],a[3]) && a[3]<=4096) {
            unsigned n=0;while(n<64 && ((char *)a[0])[n])++n;
            if(n<64)result=bios_file_read((char *)a[0],a[1],(void *)a[2],a[3]);
        }break;
    case BIOS_FILE_SIZE:
        if(pointer_ok(a[0],64)) {
            unsigned n=0;while(n<64 && ((char *)a[0])[n])++n;
            if(n<64)result=bios_file_size((char *)a[0]);
        }break;
    case BIOS_FS_OPEN:
        if(pointer_ok(a[0],64)) {
            unsigned n=0;while(n<64 && ((char *)a[0])[n])++n;
            if(n<64)result=bios_fs_open((char *)a[0],a[1]);
        }break;
    case BIOS_FS_CLOSE:result=bios_fs_close((int)a[0]);break;
    case BIOS_FS_READ:
        if(pointer_ok(a[1],a[2]) && a[2]<=4096)result=bios_fs_read((int)a[0],(void *)a[1],a[2]);
        break;
    case BIOS_FS_WRITE:
        if(pointer_ok(a[1],a[2]) && a[2]<=4096)result=bios_fs_write((int)a[0],(const void *)a[1],a[2]);
        break;
    case BIOS_FS_SEEK:result=bios_fs_seek((int)a[0],(int)a[1],(int)a[2]);break;
    case BIOS_FS_STAT:
        if(pointer_ok(a[0],64) && pointer_ok(a[1],sizeof(struct bios_fs_stat))) {
            unsigned n=0;while(n<64 && ((char *)a[0])[n])++n;
            if(n<64)result=bios_fs_stat((char *)a[0],(struct bios_fs_stat *)a[1]);
        }break;
    case BIOS_FS_MKDIR:
        if(pointer_ok(a[0],64)) {
            unsigned n=0;while(n<64 && ((char *)a[0])[n])++n;
            if(n<64)result=bios_fs_mkdir((char *)a[0]);
        }break;
    case BIOS_FS_UNLINK:
        if(pointer_ok(a[0],64)) {
            unsigned n=0;while(n<64 && ((char *)a[0])[n])++n;
            if(n<64)result=bios_fs_unlink((char *)a[0]);
        }break;
    case BIOS_FS_RENAME:
        if(pointer_ok(a[0],64) && pointer_ok(a[1],64)) {
            unsigned n=0;while(n<64 && ((char *)a[0])[n])++n;
            unsigned m=0;while(m<64 && ((char *)a[1])[m])++m;
            if(n<64 && m<64)result=bios_fs_rename((char *)a[0],(char *)a[1]);
        }break;
    case BIOS_IO_READ:if(pointer_ok(a[0],sizeof(struct bios_io))) {struct bios_io v={hal_buttons_read(),hal_switches_read()};memcpy((void *)a[0],&v,sizeof(v));result=0;}break;
    case BIOS_LEDS:hal_leds_set(a[0]);result=0;break;
    case BIOS_FLASH_READ:
        if(a[2]<=4096 && pointer_ok(a[1],a[2]))result=hal_flash_read(a[0],(void *)a[1],a[2])==HAL_OK?0:-1;
        break;
    case BIOS_RGB:result=hal_ws2812_set(a[0]>>16,a[0]>>8,a[0])==HAL_OK?0:-1;break;
    case BIOS_MOUSE:if(pointer_ok(a[0],sizeof(struct bios_mouse)))result=bios_mouse_take((void *)a[0]);break;
    case BIOS_KEY:if(pointer_ok(a[0],sizeof(struct bios_key)))result=bios_key_take((void *)a[0]);break;
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
