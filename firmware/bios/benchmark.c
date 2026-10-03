/* Wall-clock benchmarks: interrupts and LCD scanout stay enabled. No storage writes. */
#include "internal.h"
#include "payload.h"
#include <generated/csr.h>
#include <generated/soc.h>
#include <string.h>

/* Separate from BIOS image staging, payload, stack and framebuffer windows. */
#define SOURCE ((volatile uint32_t *)0x46000000u)
#define DEST ((volatile uint32_t *)0x46200000u)
#define MAX_BYTES (1024u*1024u)
static volatile uint32_t sink;
static unsigned failures;
static void libc_check(void) {
    _Alignas(4) uint8_t a[288],b[288];unsigned ok=1,cases=0;
    const unsigned lengths[]={0,1,2,3,4,7,8,15,16,17,31,32,33,63,64,65,255,256,257};
    for(unsigned i=0;i<sizeof(a);++i)a[i]=(uint8_t)(i*173u+i/7u);
    for(unsigned so=0;so<4;++so)for(unsigned d=0;d<4;++d)
        for(unsigned k=0;k<sizeof(lengths)/sizeof(lengths[0]);++k) {
            unsigned n=lengths[k];memset(b,0x7c,sizeof(b));
            if(memcpy(b+d,a+so,n)!=b+d || memcmp(a+so,b+d,n))ok=0;
            for(unsigned i=0;i<sizeof(b);++i)if(b[i]!=(i>=d && i<d+n?a[so+i-d]:0x7c))ok=0;
            if(n) {b[d+n/2]^=0x80;int diff=(int)a[so+n/2]-(int)b[d+n/2];if(memcmp(a+so,b+d,n)!=diff)ok=0;}
            memset(b,0x7c,sizeof(b));if(memset(b+d,0x1a5,n)!=b+d)ok=0;
            for(unsigned i=0;i<sizeof(b);++i)if(b[i]!=(i>=d && i<d+n?0xa5:0x7c))ok=0;
            ++cases;
        }
    bios_puts(ok?"LIBC TEST PASS cases=":"LIBC TEST FAIL cases=");bios_decimal(cases);bios_puts("\r\n");
    if(!ok)++failures;
}
static void cache_sync(void) {
    __asm__ volatile("fence rw,rw":::"memory");
#if MINI_CPU_DCACHE
    __asm__ volatile(".word 0x0000500f":::"memory");
#endif
}
static void drain(void) {
    while(!uart_txempty_read()) {}
    /* txempty covers FIFO, leave the final character time outside measurements. */
    unsigned start=hal_ticks();while((unsigned)(hal_ticks()-start)<CONFIG_CLOCK_FREQUENCY/10000u) {}
}
static void result(const char *name,unsigned size,unsigned count,unsigned ticks,unsigned check,unsigned ok) {
    if(!ok)++failures;
    bios_puts("BENCH ");bios_puts(name);bios_puts(" size=");bios_decimal(size);
    bios_puts(" count=");bios_decimal(count);bios_puts(" ticks=");bios_decimal(ticks);
    bios_puts(" check=");bios_hex(check);bios_puts(ok?" PASS\r\n":" FAIL\r\n");
    hal_poll();
}
static unsigned integer(unsigned n) {
    unsigned x=0x12345678;
    for(unsigned i=0;i<n;++i) {
        /* Six dependent RV32 integer operations per iteration, plus loop control. */
        __asm__ volatile("slli t0,%0,13\nxor %0,%0,t0\nsrli t0,%0,17\nxor %0,%0,t0\nslli t0,%0,5\nxor %0,%0,t0":"+r"(x)::"t0");
    }
    return x;
}
static void cpu(void) {
    unsigned n=500000,start=hal_ticks(),x=integer(n),ticks=hal_ticks()-start;
    unsigned reference=0x12345678;
    for(unsigned i=0;i<n;++i) {reference^=reference<<13;reference^=reference>>17;reference^=reference<<5;}
    sink=x;result("cpu.xorshift",6,n,ticks,x,x==reference);
    x=1;start=hal_ticks();
    for(unsigned i=0;i<100000;++i) __asm__ volatile("mul %0,%0,%1\naddi %0,%0,1":"+r"(x):"r"(1664525u));
    ticks=hal_ticks()-start;reference=1;
    for(unsigned i=0;i<100000;++i)reference=reference*1664525u+1;
    sink=x;result("cpu.mul-add",2,100000,ticks,x,x==reference);
#if MINI_FEATURE_FPU
    unsigned bits=0,one=0x3f800000;
    start=hal_ticks();
    __asm__ volatile("fmv.w.x ft0,zero\nfmv.w.x ft1,%2\n1: fadd.s ft0,ft0,ft1\naddi %0,%0,-1\nbnez %0,1b\nfmv.x.w %1,ft0":"+&r"(n),"=r"(bits):"r"(one):"ft0","ft1");
    ticks=hal_ticks()-start;sink=bits;
    result("cpu.fadd-dependent",1,500000,ticks,bits,bits==0x48f42400u);
#endif
}
static void memory(void) {
    for(unsigned i=0;i<MAX_BYTES/4;++i)SOURCE[i]=i;
    const unsigned sizes[]={1024,4096,65536,MAX_BYTES};
    for(unsigned k=0;k<4;++k) {
        unsigned bytes=sizes[k],words=bytes/4,reps=MAX_BYTES/bytes,sum=0;
        cache_sync();unsigned start=hal_ticks();
        for(unsigned r=0;r<reps;++r)for(unsigned i=0;i<words;++i)sum+=SOURCE[i];
        unsigned ticks=hal_ticks()-start;sink=sum;
        unsigned expected=(unsigned)(((uint64_t)words*(words-1)/2)*reps);
        result("mem.read32",bytes,reps,ticks,sum,sum==expected);
        start=hal_ticks();
        for(unsigned r=0;r<reps;++r)for(unsigned i=0;i<words;++i)DEST[i]=i;
        __asm__ volatile("fence rw,rw":::"memory");ticks=hal_ticks()-start;
        unsigned ok=1;for(unsigned i=0;i<words;++i)if(DEST[i]!=i)ok=0;
        result("mem.write32",bytes,reps,ticks,DEST[words-1],ok);
        cache_sync();start=hal_ticks();
        for(unsigned r=0;r<reps;++r)for(unsigned i=0;i<words;++i)DEST[i]=SOURCE[i];
        __asm__ volatile("fence rw,rw":::"memory");ticks=hal_ticks()-start;
        ok=1;for(unsigned i=0;i<words;++i)if(DEST[i]!=i)ok=0;
        result("mem.copy32",bytes,reps,ticks,DEST[words-1],ok);
    }
    /* Full-cycle, cache-line-strided dependent load chain in a 1 MiB working set. */
    for(unsigned i=0;i<MAX_BYTES/4;++i)SOURCE[i]=(i+4097u)&(MAX_BYTES/4-1);
    cache_sync();unsigned at=0,start=hal_ticks();
    for(unsigned i=0;i<MAX_BYTES/4;++i)at=SOURCE[at];
    unsigned ticks=hal_ticks()-start;sink=at;
    result("mem.chase32",MAX_BYTES,MAX_BYTES/4,ticks,at,at==0);
    memset((void *)SOURCE,0x5a,MAX_BYTES);cache_sync();start=hal_ticks();
    memcpy((void *)DEST,(const void *)SOURCE,MAX_BYTES);cache_sync();ticks=hal_ticks()-start;
    result("mem.libc-copy",MAX_BYTES,1,ticks,DEST[0],memcmp((void *)DEST,(const void *)SOURCE,MAX_BYTES)==0);
    start=hal_ticks();memset((void *)DEST,0xa5,MAX_BYTES);cache_sync();ticks=hal_ticks()-start;
    unsigned ok=1;for(unsigned i=0;i<MAX_BYTES/4;++i)if(DEST[i]!=0xa5a5a5a5u)ok=0;
    result("mem.libc-set",MAX_BYTES,1,ticks,DEST[0],ok);
}
static void sd_benchmark(unsigned hz) {
#if MINI_FEATURE_SD
    if(hal_sd_init()!=HAL_OK) {result("io.sd-read",0,0,0,0,0);return;}
    unsigned baseline=0;
    if(hz) {
        /* Read at the qualified low speed first, then compare every test at the
         * requested speed with this reference, not merely with each other. */
        if(hal_sd_set_read_clock(7500000u)!=HAL_OK) {result("io.sd-clock",hz,0,0,0,0);return;}
        for(unsigned sector=0;sector<2048;sector+=8) {
            if(hal_sd_read(sector,(void *)(SOURCE+sector*128),8)!=HAL_OK) {result("io.sd-reference",0,0,0,0,0);return;}
            hal_poll();
        }
        baseline=bios_crc((const void *)SOURCE,MAX_BYTES);
        hal_result_t r=hal_sd_set_read_clock(hz);
        if(r!=HAL_OK) {result("io.sd-clock",hz,0,0,r,0);return;}
    }
    hal_sd_info_t info;hal_sd_get_info(&info);
    bios_puts("BENCH SD hz=");bios_decimal(info.clock_hz);bios_puts(" width=");bios_decimal(info.bus_width);
    bios_puts(" read_hz=");bios_decimal(info.read_clock_hz);bios_puts(" write_hz=");bios_decimal(info.write_clock_hz);
    bios_puts(" hs=");bios_decimal(info.high_speed);bios_puts("\r\n");
    for(unsigned blocks=1;blocks<=8;blocks*=8) {
        unsigned bytes=blocks*512,start=hal_ticks(),ok=1;
        for(unsigned sector=0;sector<2048;sector+=blocks) {
            if(hal_sd_read(sector,(void *)(DEST+sector*128),blocks)!=HAL_OK) {ok=0;break;}
            hal_poll();
        }
        unsigned ticks=hal_ticks()-start,crc=bios_crc((const void *)DEST,MAX_BYTES);
        if(!hz && blocks==1)baseline=crc;
        if(baseline!=crc)ok=0;
        result("io.sd-read",bytes,2048/blocks,ticks,crc,ok);
        if(!ok)break;
    }
#else
    (void)hz;bios_puts("SD benchmark unsupported\r\n");++failures;
#endif
}
static void io(void) {
#if MINI_FEATURE_FLASH
    uint32_t id;unsigned capacity;
    if(hal_flash_probe(&id,&capacity)==HAL_OK) {
        unsigned start=hal_ticks();int ok=hal_flash_read(0,(void *)DEST,65536)==HAL_OK;
        unsigned ticks=hal_ticks()-start,crc=bios_crc_update(~0u,(const void *)DEST,65536)^~0u;
        int again=hal_flash_read(0,(void *)DEST,65536)==HAL_OK;
        result("io.flash-read",65536,1,ticks,crc,ok && again && crc==(bios_crc_update(~0u,(const void *)DEST,65536)^~0u));
    } else result("io.flash-read",65536,1,0,0,0);
#endif
#if MINI_FEATURE_SD
    sd_benchmark(0);
#endif
#if MINI_FEATURE_VIDEO
    {
    volatile uint16_t *frame=hal_video_frame(rgb_lcd_active_read()^1u);unsigned start=hal_ticks();
    for(unsigned i=0;i<480u*272u;++i)frame[i]=(uint16_t)i;
    __asm__ volatile("fence rw,rw":::"memory");unsigned ticks=hal_ticks()-start;
    unsigned ok=1;for(unsigned i=0;i<480u*272u;++i)if(frame[i]!=(uint16_t)i)ok=0;
    result("io.rgb-fill16",480u*272u*2u,1,ticks,frame[480u*272u-1],ok);
    }
#endif
    memset((void *)DEST,'.',4096);drain();unsigned start=hal_ticks();size_t written=0;
    int ok=hal_uart_write((void *)DEST,4096,2000,&written)==HAL_OK;drain();
    unsigned ticks=hal_ticks()-start;bios_puts("\r\n");result("io.uart-tx",4096,1,ticks,written,ok && written==4096);
}
int bios_benchmark_command(const char *s) {
    if(strcmp(s,"bench") && strncmp(s,"bench ",6))return 0;
    const char *which=s[5]?s+6:"all";
    unsigned hz=0;
    if(!strncmp(which,"sd ",3)) {
        if(!strcmp(which+3,"7500000"))hz=7500000;
        else if(!strcmp(which+3,"10000000"))hz=10000000;
        else if(!strcmp(which+3,"15000000"))hz=15000000;
        else if(!strcmp(which+3,"30000000"))hz=30000000;
    }
    if(strcmp(which,"all") && strcmp(which,"cpu") && strcmp(which,"mem") && strcmp(which,"libc") && strcmp(which,"io") && strncmp(which,"net ",4) && !hz) {
        bios_puts("bench [all|cpu|mem|libc|io|net FILE|sd 7500000/10000000/15000000/30000000]\r\n");return 1;
    }
    failures=0;bios_puts("BENCH BEGIN clock_hz=");bios_decimal(CONFIG_CLOCK_FREQUENCY);
    bios_puts(" interrupts=on scanout=on compiler=Os\r\n");
    bios_video_mode(BIOS_GRAPHICS);drain();
    if(!strcmp(which,"all") || !strcmp(which,"mem") || !strcmp(which,"libc"))libc_check();
    if(!strcmp(which,"all") || !strcmp(which,"cpu"))cpu();
    if(!strcmp(which,"all") || !strcmp(which,"mem"))memory();
    if(!strcmp(which,"all") || !strcmp(which,"io"))io();
    if(hz)sd_benchmark(hz);
    if(!strncmp(which,"net ",4)) {
        unsigned length=0,start=hal_ticks();
        int ok=bios_tftp_get(bios_settings.server,which+4,(void *)SOURCE,MAX_BYTES,&length);
        unsigned ticks=hal_ticks()-start;
        result("io.tftp-rx",length,1,ticks,bios_crc_update(~0u,(void *)SOURCE,length)^~0u,ok);
    }
    bios_video_mode(BIOS_TEXT);bios_puts(failures?"BENCH DONE FAIL\r\n":"BENCH DONE PASS\r\n");return 1;
}
