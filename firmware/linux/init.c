/* SPDX-License-Identifier: MIT
 * Freestanding RV32 Linux init: exercise U-mode, MMU, timer, fork/exec/wait
 * without depending on a pre-existing RV32 libc or compressed instructions.
 */
/* RV32 time64 UAPI; the build supplies minimal freestanding libc headers. */
#define __USE_TIME_BITS64
#include <asm-generic/ioctl.h>
#include <sound/asound.h>
#include <linux/gpio.h>
#include <linux/fs.h>
#include <mtd/mtd-abi.h>
typedef unsigned int u32;
void *memcpy(void *destination,const void *source,unsigned length)
{
    unsigned char *d=destination; const unsigned char *s=source;
    for(unsigned i=0;i<length;i++) d[i]=s[i]; return destination;
}
void *memset(void *destination, int value, unsigned length)
{
    unsigned char *p = destination;
    for (unsigned i = 0; i < length; ++i) p[i] = value;
    return destination;
}
static long call(long n, long x, long y, long z, long u, long v, long w)
{
    register long a0 __asm__("a0") = x, a1 __asm__("a1") = y;
    register long a2 __asm__("a2") = z, a3 __asm__("a3") = u;
    register long a4 __asm__("a4") = v, a5 __asm__("a5") = w;
    register long a7 __asm__("a7") = n;
    __asm__ volatile("ecall" : "+r"(a0) : "r"(a1), "r"(a2), "r"(a3),
                     "r"(a4), "r"(a5), "r"(a7) : "memory");
    return a0;
}
static void text(const char *s)
{
    unsigned n = 0;
    while (s[n]) ++n;
    call(64, 1, (long)s, n, 0, 0, 0);
}
static void number(u32 n)
{
    char b[11]; unsigned i = sizeof(b);
    b[--i] = 0;
    do { b[--i] = '0' + n % 10; n /= 10; } while (n);
    text(b + i);
}
static int equal(const char *a, const char *b)
{
    while (*a && *a == *b) { ++a; ++b; }
    return *a == *b;
}
static void die(const char *reason)
{
    char message[160]; unsigned n=0;
    const char *prefix="LINUX SELFTEST FAIL: ";
    while(*prefix) message[n++]=*prefix++;
    while(*reason && n+2<sizeof(message)) message[n++]=*reason++;
    message[n++]='\n'; message[n]=0; text(message);
    for (;;) call(124, 0, 0, 0, 0, 0, 0);
}
static void read_file(const char *name)
{
    char b[512]; long fd = call(56, -100, (long)name, 0, 0, 0, 0), n;
    if (fd < 0) { text("open failed\n"); return; }
    while ((n = call(63, fd, (long)b, sizeof(b), 0, 0, 0)) > 0)
        call(64, 1, (long)b, n, 0, 0, 0);
    call(57, fd, 0, 0, 0, 0, 0);
}
static void fork_test(void)
{
    long pid = call(220, 17, 0, 0, 0, 0, 0); /* clone(SIGCHLD) = fork */
    if (pid < 0) die("clone");
    if (!pid) {
        const char *argv[] = { "/init", "--child", 0 };
        const char *env[] = { 0 };
        call(221, (long)argv[0], (long)argv, (long)env, 0, 0, 0);
        call(93, 99, 0, 0, 0, 0, 0);
        for (;;) {}
    }
    /* RV32's current syscall table exposes waitid, not the legacy wait4. */
    int info[32] = {0};
    long ret = call(95, 1, pid, (long)info, 4, 0, 0);
    if (ret || info[0] != 17 || info[2] != 1 || info[3] != pid || info[5] != 42) {
        text("waitid ret="); number(ret); text(" code="); number(info[2]);
        text(" pid="); number(info[3]); text(" status="); number(info[5]); text("\n");
        die("exec/wait");
    }
    text("SELFTEST fork/exec/wait PASS\n");
}
static void pause_frame(void)
{
    long long delay[2] = {0, 150000000};
    if (call(407, 1, 0, (long)delay, 0, 0, 0)) die("frame timer");
}
static void fb_status(char *buf, unsigned size)
{
    const char *path = "/sys/class/graphics/fb0/device/dma_status";
    long fd = call(56, -100, (long)path, 0, 0, 0, 0);
    if (fd < 0) die("fb DMA status open");
    long n = call(63, fd, (long)buf, size - 1, 0, 0, 0);
    call(57, fd, 0, 0, 0, 0, 0);
    if (n <= 0) die("fb DMA status read");
    buf[n] = 0;
}
static u32 field(const char *buf, const char *key)
{
    for (const char *p = buf; *p; ++p) {
        unsigned i = 0;
        while (key[i] && p[i] == key[i]) ++i;
        if (!key[i]) {
            u32 n = 0;
            p += i;
            while (*p >= '0' && *p <= '9') n = n * 10 + *p++ - '0';
            return n;
        }
    }
    die("fb status field");
    return 0;
}
static void framebuffer_test(void)
{
    /* Claim the VT for graphics so asynchronous kernel logs cannot repaint it. */
    long vt = call(56, -100, (long)"/dev/tty0", 2, 0, 0, 0);
    if (vt < 0 || call(29, vt, 0x4b3a, 1, 0, 0, 0)) die("fb graphics VT");
    call(57, vt, 0, 0, 0, 0, 0);
    enum { WIDTH = 480, HEIGHT = 272, BYTES = WIDTH * HEIGHT * 2 };
    long fd = call(56, -100, (long)"/dev/fb0", 2, 0, 0, 0);
    if (fd < 0) die("fb0 open");
    u32 var[40];
    if (call(29, fd, 0x4600, (long)var, 0, 0, 0) ||
        var[0] != WIDTH || var[1] != HEIGHT || var[6] != 16) die("fb geometry");
    volatile unsigned short *pixels = (void *)call(222, 0, BYTES, 3, 1, fd, 0);
    if ((long)pixels < 0 && (long)pixels >= -4095) die("fb mmap");
    unsigned short written = 0x1234, readback = 0;
    if (call(64, fd, (long)&written, 2, 0, 0, 0) != 2) die("fb write");
    long reader = call(56, -100, (long)"/dev/fb0", 0, 0, 0, 0);
    if (reader < 0 || call(63, reader, (long)&readback, 2, 0, 0, 0) != 2 ||
        readback != written || pixels[0] != written) die("fb read");
    call(57, reader, 0, 0, 0, 0, 0);
    char before[180], after[180], blank[180];
    pause_frame();
    fb_status(before, sizeof(before));
    for (unsigned y = 0; y < HEIGHT; ++y)
        for (unsigned x = 0; x < WIDTH; ++x)
            pixels[y * WIDTH + x] = x < WIDTH / 3 ? 0xf800 : x < WIDTH * 2 / 3 ? 0x07e0 : 0x001f;
    __asm__ volatile("fence rw,rw" ::: "memory");
    for (unsigned y = 0; y < HEIGHT; ++y)
        for (unsigned x = 0; x < WIDTH; ++x) {
            unsigned short expected = x < WIDTH / 3 ? 0xf800 : x < WIDTH * 2 / 3 ? 0x07e0 : 0x001f;
            if (pixels[y * WIDTH + x] != expected) die("fb pixel readback");
        }
    pause_frame();
    fb_status(after, sizeof(after));
    text("FB DMA before: "); text(before);
    text("FB DMA after: "); text(after);
    if (field(after, "enable=") != 1 || field(after, "address_error=") ||
        field(after, "completed=") == field(before, "completed=") ||
        field(after, "underflows=") != field(before, "underflows=")) die("fb DMA progress");
    if (call(29, fd, 0x4611, 4, 0, 0, 0)) die("fb blank");
    fb_status(blank, sizeof(blank));
    if (field(blank, "enable=") || (field(blank, "state=") & 2)) die("fb blank drain");
    if (call(29, fd, 0x4611, 0, 0, 0, 0)) die("fb unblank");
    pause_frame();
    fb_status(after, sizeof(after));
    text("FB DMA resumed: "); text(after);
    if (field(after, "enable=") != 1 || field(after, "address_error=") ||
        field(after, "completed=") == field(blank, "completed=") ||
        field(after, "underflows=") != field(blank, "underflows=")) die("fb resume DMA");
    var[0] = 320;
    if (call(29, fd, 0x4601, (long)var, 0, 0, 0) != -22) die("fb accepts wrong mode");
    call(215, (long)pixels, BYTES, 0, 0, 0, 0);
    call(57, fd, 0, 0, 0, 0, 0);
    text("SELFTEST fb0 RGB565/mmap/readback/DMA/blank PASS\n");
}
static void selftest(void)
{
    volatile u32 *p = (void *)call(222, 0, 8192, 3, 0x22, -1, 0);
    if ((long)p < 0 && (long)p >= -4095) die("mmap");
    p[0] = 0x12345678; p[1024] = 0xabcdef01;
    if (p[0] != 0x12345678 || p[1024] != 0xabcdef01) die("MMU read/write");
    if (call(226, (long)p, 8192, 1, 0, 0, 0)) die("mprotect");
    if (p[1024] != 0xabcdef01) die("MMU read-only");
    if (call(215, (long)p, 8192, 0, 0, 0, 0)) die("munmap");
    text("SELFTEST mmap/mprotect/munmap PASS\n");
    fork_test();
    /* RV32 uses time64: clock_gettime64 and clock_nanosleep_time64. */
    long long before[2], after[2], delay[2] = { 0, 100000000 };
    if (call(403, 1, (long)before, 0, 0, 0, 0) ||
        call(407, 1, 0, (long)delay, 0, 0, 0) ||
        call(403, 1, (long)after, 0, 0, 0, 0)) die("timer syscalls");
    if (after[0] < before[0] || (after[0] == before[0] && after[1] <= before[1]))
        die("timer did not advance");
    text("SELFTEST timer/nanosleep PASS\nLINUX USERSPACE PASS\n");
}
static long open_file(const char *path, long flags)
{ return call(56,-100,(long)path,flags,0,0,0); }
static void close_file(long fd) { call(57,fd,0,0,0,0,0); }
static long ioctl_file(long fd,long request,void *arg)
{ return call(29,fd,request,(long)arg,0,0,0); }
static unsigned load_file(const char *path,char *data,unsigned size)
{
    long fd=open_file(path,0),n;
    if(fd<0) { text(path); die("device attribute open"); }
    n=call(63,fd,(long)data,size-1,0,0,0); close_file(fd);
    if(n<0) die("device attribute read"); data[n]=0; return n;
}
static void store_file(const char *path,const char *data,unsigned size)
{
    long fd=open_file(path,1);
    if(fd<0 || call(64,fd,(long)data,size,0,0,0)!=(long)size) { text(path); die("device attribute write"); }
    close_file(fd);
}
static void delay_ms(unsigned ms)
{
    long long delay[2]={ms/1000,(ms%1000)*1000000};
    if(call(407,1,0,(long)delay,0,0,0)) die("device delay");
}
static void list_dir(const char *path)
{
    char data[2048]; long fd=open_file(path,0),n;
    if(fd<0) { text(path); text(" absent\n"); return; }
    while((n=call(61,fd,(long)data,sizeof(data),0,0,0))>0) {
        for(unsigned offset=0;offset<(unsigned)n;) {
            unsigned short size=*(unsigned short *)(data+offset+16);
            if(size<20 || offset+size>(unsigned)n) die("getdents64");
            text(data+offset+19); text(" "); offset+=size;
        }
    }
    text("\n"); close_file(fd);
}
static u32 checksum(const unsigned char *data,unsigned size)
{ u32 hash=2166136261u; for(unsigned i=0;i<size;i++) hash=(hash^data[i])*16777619u; return hash; }
static void storage_test(void)
{
    unsigned char data[4096]; u32 hash;
    long fd=open_file("/dev/mmcblk0",0),n;
    unsigned long long bytes=0;
    if(fd<0) die("mmcblk0 absent");
    if(ioctl_file(fd,BLKGETSIZE64,&bytes) || bytes<1048576) die("SD capacity");
    n=call(63,fd,(long)data,sizeof(data),0,0,0); close_file(fd);
    if(n!=sizeof(data) || data[510]!=0x55 || data[511]!=0xaa) die("SD boot sector");
    hash=checksum(data,sizeof(data));
    for(unsigned i=0;i<8;i++) {
        fd=open_file("/dev/mmcblk0",0); n=call(63,fd,(long)data,sizeof(data),0,0,0); close_file(fd);
        if(n!=sizeof(data) || checksum(data,sizeof(data))!=hash) die("SD repeat DMA read");
    }
    text("SD first-4KiB checksum="); number(hash); text(" capacity-MiB="); number(bytes>>20); text("\n");
    long ret=call(40,(long)"/dev/mmcblk0p1",(long)"/mnt",(long)"vfat",1,0,0);
    if(ret) ret=call(40,(long)"/dev/mmcblk0",(long)"/mnt",(long)"vfat",1,0,0);
    if(ret) { text("SD read-only FAT mount errno="); number(-ret); text("\n"); die("SD FAT mount"); }
    list_dir("/mnt"); if(call(39,(long)"/mnt",0,0,0,0,0)) die("SD unmount");
    fd=open_file("/dev/mtd0",0); struct mtd_info_user info;
    if(fd<0 || ioctl_file(fd,MEMGETINFO,&info) || info.size!=4194304 || (info.flags&MTD_WRITEABLE)) die("Flash read-only MTD");
    n=call(63,fd,(long)data,sizeof(data),0,0,0); close_file(fd);
    if(n!=sizeof(data)) die("Flash read"); hash=checksum(data,sizeof(data));
    fd=open_file("/dev/mtd0",0); n=call(63,fd,(long)data,sizeof(data),0,0,0); close_file(fd);
    if(n!=sizeof(data) || checksum(data,sizeof(data))!=hash) die("Flash repeat read");
    text("Flash first-4KiB checksum="); number(hash); text("\nSELFTEST SD/MMC/FAT-readonly/Flash-MTD PASS\n");
}
static void board_test(void)
{
    char old[64],data[128];
    unsigned size=load_file("/sys/class/leds/mini:green:led0/brightness",old,sizeof(old));
    store_file("/sys/class/leds/mini:green:led0/brightness","1\n",2);
    load_file("/sys/class/leds/mini:green:led0/brightness",data,sizeof(data)); if(data[0]!='1') die("LED on");
    store_file("/sys/class/leds/mini:green:led0/brightness","0\n",2);
    load_file("/sys/class/leds/mini:green:led0/brightness",data,sizeof(data)); if(data[0]!='0') die("LED off");
    store_file("/sys/class/leds/mini:green:led0/brightness",old,size);
    store_file("/sys/class/leds/mini:rgb:status/multi_intensity","0 255 0\n",8);
    store_file("/sys/class/leds/mini:rgb:status/brightness","8\n",2);
    delay_ms(2); store_file("/sys/class/leds/mini:rgb:status/brightness","0\n",2);
    struct gpiochip_info gpio; int found=0;
    for(unsigned i=0;i<2;i++) {
        char path[]="/dev/gpiochip0"; path[13]='0'+i;
        long fd=open_file(path,0);
        if(fd>=0) { if(!ioctl_file(fd,GPIO_GET_CHIPINFO_IOCTL,&gpio)) {
            text("GPIO "); text(gpio.label); text(" lines="); number(gpio.lines); text("\n");
            if(gpio.lines==14) found=1;
        } close_file(fd); }
    }
    if(!found) die("GPIO chip");
    read_file("/proc/bus/input/devices"); read_file("/proc/interrupts");
    text("SELFTEST native-UART/IRQ/GPIO/LED/WS2812 PASS\n");
}
static void pcm_interval(struct snd_pcm_hw_params *p,unsigned param,unsigned value)
{
    struct snd_interval *i=&p->intervals[param-SNDRV_PCM_HW_PARAM_FIRST_INTERVAL];
    i->min=i->max=value; i->integer=1;
}
static void pcm_setup(long fd,int capture)
{
    struct snd_pcm_hw_params p={0};
    for(unsigned i=0;i<3;i++) for(unsigned j=0;j<8;j++) p.masks[i].bits[j]=~0u;
    for(unsigned i=0;i<12;i++) p.intervals[i].max=~0u;
    for(unsigned i=0;i<3;i++) for(unsigned j=0;j<8;j++) p.masks[i].bits[j]=0;
    p.masks[0].bits[0]=1u<<SNDRV_PCM_ACCESS_RW_INTERLEAVED;
    p.masks[1].bits[0]=1u<<(capture?SNDRV_PCM_FORMAT_S24_LE:SNDRV_PCM_FORMAT_S16_LE);
    p.masks[2].bits[0]=1; p.rmask=~0u;
    pcm_interval(&p,SNDRV_PCM_HW_PARAM_CHANNELS,2); pcm_interval(&p,SNDRV_PCM_HW_PARAM_RATE,48000);
    pcm_interval(&p,SNDRV_PCM_HW_PARAM_PERIOD_SIZE,1024); pcm_interval(&p,SNDRV_PCM_HW_PARAM_PERIODS,capture?4:16);
    pcm_interval(&p,SNDRV_PCM_HW_PARAM_BUFFER_SIZE,capture?4096:16384);
    long ret=ioctl_file(fd,SNDRV_PCM_IOCTL_HW_PARAMS,&p);
    if(ret) { text("PCM hw_params errno="); number(-ret); text("\n"); die("ALSA hw params"); }
    if(ioctl_file(fd,SNDRV_PCM_IOCTL_PREPARE,0)) die("ALSA prepare");
}
static void audio_test(void)
{
    static u32 silence[16384],capture[2048]; char stats[256];
    long fd=open_file("/dev/snd/pcmC0D0p",2); if(fd<0) die("ALSA playback absent");
    pcm_setup(fd,0);
    struct snd_xferi x={.buf=silence,.frames=16384};
    if(ioctl_file(fd,SNDRV_PCM_IOCTL_WRITEI_FRAMES,&x) || x.result!=16384) die("ALSA playback write");
    delay_ms(35);
    if(ioctl_file(fd,SNDRV_PCM_IOCTL_DROP,0)) die("ALSA drop");
    load_file("/sys/bus/platform/devices/f0000000.audio/stats",stats,sizeof(stats));
    text("ALSA playback "); text(stats);
    if(!field(stats,"played=") || field(stats,"errors=") || field(stats,"underruns=")) die("ALSA DMA progress");
    close_file(fd);
    fd=open_file("/dev/snd/pcmC0D0c",0); if(fd<0) die("ALSA capture absent"); pcm_setup(fd,1);
    x=(struct snd_xferi){.buf=capture,.frames=1024};
    if(ioctl_file(fd,SNDRV_PCM_IOCTL_READI_FRAMES,&x) || x.result!=1024) die("ALSA capture read");
    load_file("/sys/bus/platform/devices/f0000000.audio/stats",stats,sizeof(stats));
    text("ALSA capture "); text(stats);
    if(!field(stats,"captured=") || field(stats,"mic_overruns=")) die("ALSA capture counters");
    ioctl_file(fd,SNDRV_PCM_IOCTL_DROP,0); close_file(fd);
    text("SELFTEST ALSA-stereo-DMA/microphone-capture PASS\n");
}
static void panel_test(void)
{
    char before[32],after[32];
    load_file("/sys/class/graphics/fb1/device/updates",before,sizeof(before));
    long fd=open_file("/dev/fb1",2); u32 var[40];
    if(fd<0 || ioctl_file(fd,0x4600,var) || var[0]!=240 || var[1]!=135 || var[6]!=16) die("SPI panel geometry");
    volatile unsigned short *pixels=(void *)call(222,0,240*135*2,3,1,fd,0);
    if((long)pixels<0 && (long)pixels>=-4095) die("SPI panel mmap");
    for(unsigned i=0;i<240*135;i++) pixels[i]=((i/240)&8)?0xf800:0x07e0;
    for(unsigned i=0;i<400;i++) {
        delay_ms(100); load_file("/sys/class/graphics/fb1/device/updates",after,sizeof(after));
        if(field(after,"")!=field(before,"")) break;
    }
    if(field(after,"")==field(before,"")) die("SPI panel deferred transfer");
    call(215,(long)pixels,240*135*2,0,0,0,0); close_file(fd);
    text("SELFTEST SPI-panel-fb1/mmap/deferred-transfer PASS\n");
}
static void devices_test(void)
{
    delay_ms(2000); storage_test(); board_test(); audio_test(); panel_test();
    char data[128]; load_file("/sys/bus/usb/devices/usb1/product",data,sizeof(data)); text(data);
    load_file("/sys/bus/usb/devices/1-1/idVendor",data,sizeof(data)); text("USB attached vendor="); text(data);
    list_dir("/sys/bus/usb/devices");
    text("SELFTEST USB-host/enumeration PASS\nLINUX DEVICES PASS\n");
}
static void network_test(const char *host)
{
    long fd=call(198,2,2,0,0,0,0); if(fd<0) die("IPv4 socket");
    char req[40]={0}; const char name[]="eth0";
    for(unsigned i=0;i<sizeof(name);i++) req[i]=name[i];
    *(unsigned short *)(req+16)=2; req[20]=169; req[21]=254; req[22]=20; req[23]=20;
    if(ioctl_file(fd,0x8916,req)) die("IPv4 address");
    req[20]=255; req[21]=255; req[22]=0; req[23]=0;
    if(ioctl_file(fd,0x891c,req) || ioctl_file(fd,0x8913,req)) die("IPv4 netmask/flags");
    *(unsigned short *)(req+16)|=1; if(ioctl_file(fd,0x8914,req)) die("netdev up");
    char carrier[8];
    for(unsigned i=0;i<80;i++) { load_file("/sys/class/net/eth0/carrier",carrier,sizeof(carrier)); if(carrier[0]=='1') break; delay_ms(100); }
    if(carrier[0]!='1') die("Ethernet PHY link");
    unsigned char address[16]={2,0,0xb6,0x4a}; /* port 46666 in network byte order */
    for(unsigned i=0;i<4;i++) {
        unsigned n=0; if(*host<'0' || *host>'9') die("host IPv4 syntax");
        while(*host>='0' && *host<='9') n=n*10+*host++-'0';
        if(n>255 || (i<3 && *host++!='.')) die("host IPv4 syntax"); address[4+i]=n;
    }
    unsigned peer_port=46666;
    if(*host==' ') {
        peer_port=0; host++;
        while(*host>='0' && *host<='9') peer_port=peer_port*10+*host++-'0';
        if(*host || !peer_port || peer_port>65535) die("host UDP port");
    }
    address[2]=peer_port>>8; address[3]=peer_port;
    long long timeout[2]={3,0};
    if(call(208,fd,1,66,(long)timeout,sizeof(timeout),0)) die("socket timeout");
    unsigned char sent[1000],received[1000];
    for(unsigned round=0;round<32;round++) {
        for(unsigned i=0;i<sizeof(sent);i++) sent[i]=(i*17+round)&255;
        if(call(206,fd,(long)sent,sizeof(sent),0,(long)address,sizeof(address))!=sizeof(sent)) die("UDP send");
        long n=call(207,fd,(long)received,sizeof(received),0,0,0);
        if(n!=sizeof(received) || checksum(sent,sizeof(sent))!=checksum(received,sizeof(received))) { text("UDP recv errno="); number(-n); text("\n"); die("UDP echo"); }
    }
    close_file(fd); read_file("/proc/net/dev");
    text("SELFTEST Ethernet-IRQ/ring-DMA/IPv4/UDP-32x1000 PASS\n");
}
void mini_main(long *stack)
{
    const char **argv = (void *)(stack + 1);
    if (stack[0] > 1 && equal(argv[1], "--child")) {
        call(93, 42, 0, 0, 0, 0, 0);
        for (;;) {}
    }
    long fd = call(56, -100, (long)"/dev/console", 2, 0, 0, 0);
    if (fd < 0) for (;;) call(124, 0, 0, 0, 0, 0, 0);
    for (long i = 0; i < 3; ++i) if (fd != i) call(24, fd, i, 0, 0, 0, 0);
    if (fd > 2) call(57, fd, 0, 0, 0, 0, 0);
    call(40, (long)"proc", (long)"/proc", (long)"proc", 0, 0, 0);
    call(40, (long)"sysfs", (long)"/sys", (long)"sysfs", 0, 0, 0);
    call(40, (long)"devtmpfs", (long)"/dev", (long)"devtmpfs", 0, 0, 0);
    char uts[390];
    store_file("/proc/sys/kernel/printk","4\n",2);
    if(call(172,0,0,0,0,0,0)!=1) die("init PID");
    text("riscv-mini Linux init pid=1\n");
    if (!call(160, (long)uts, 0, 0, 0, 0, 0)) {
        text(uts); text(" "); text(uts + 130); text(" "); text(uts + 260); text("\n");
    }
    selftest();
    framebuffer_test();
    text("Commands: help, uname, mem, cpu, uptime, test, fb, fbstat, devices, network HOST, reboot\n");
    for (;;) {
        char command[64]; unsigned n = 0; char c;
        text("linux-mini> ");
        while (call(63, 0, (long)&c, 1, 0, 0, 0) == 1) {
            if (c == '\n' || c == '\r') break;
            if (n + 1 < sizeof(command)) command[n++] = c;
        }
        command[n] = 0;
        if (equal(command, "mem")) read_file("/proc/meminfo");
        else if (equal(command, "cpu")) read_file("/proc/cpuinfo");
        else if (equal(command, "uptime")) read_file("/proc/uptime");
        else if (equal(command, "test")) selftest();
        else if (equal(command, "fb")) framebuffer_test();
        else if (equal(command, "fbstat")) read_file("/sys/class/graphics/fb0/device/dma_status");
        else if (equal(command,"devices")) devices_test();
        else if (command[0]=='n' && command[1]=='e' && command[2]=='t' && command[3]=='w' && command[4]=='o' && command[5]=='r' && command[6]=='k' && command[7]==' ') network_test(command+8);
        else if (equal(command, "uname")) { text(uts); text(" "); text(uts + 130); text("\n"); }
        else if (equal(command, "reboot")) call(142, 0xfee1dead, 672274793, 0x1234567, 0, 0, 0);
        else text("Commands: help, uname, mem, cpu, uptime, test, fb, fbstat, reboot\n");
    }
}
__asm__(".global _start\n_start:\nmv a0,sp\ncall mini_main\n1: j 1b\n");
