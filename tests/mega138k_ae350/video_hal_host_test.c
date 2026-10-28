#define _GNU_SOURCE
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include "ae350_video.h"
#include "ae350_tty.h"
#include "ae350_address_map.h"
#include "ae350_hdmi_console_demo.h"

static uint32_t regs[256],ticks,delay,clean_base,clean_bytes;
static uint32_t staged_control,staged_base,clean_calls,commit_calls;
static int frozen,reject,draining,hold_drain,clean_error,mode_frozen;

uint32_t ae350_video_test_read(unsigned off) {
    if(off==VIDEO_MODE_STATUS && regs[off/4] && !mode_frozen && --delay==0)regs[off/4]=0;
    if(off==VIDEO_COMMIT && regs[off/4] && !frozen && --delay==0) {
        regs[off/4]=0;regs[VIDEO_ACTIVE_SEQUENCE/4]=regs[VIDEO_SEQUENCE/4];
        regs[VIDEO_STATUS/4]=staged_control&3u;
        regs[VIDEO_ACTIVE_BASE/4]=staged_base;
        if(!(staged_control&1u))draining=1;
    }
    if(off==VIDEO_STATUS && draining && !hold_drain) {
        draining=0;regs[off/4]=16;
    }
    return regs[off/4];
}
void ae350_video_test_write(unsigned off,uint32_t value) {
    if(off==VIDEO_COMMIT) {
        if(reject || regs[off/4])return;
        commit_calls++;regs[off/4]=1;delay=3;regs[VIDEO_SEQUENCE/4]++;
        staged_control=regs[VIDEO_CONTROL/4];
        staged_base=regs[(staged_control&2u)?VIDEO_BASE1/4:VIDEO_BASE0/4];
    } else if(off==VIDEO_MODE_CONFIG) {
        assert(!regs[VIDEO_ENABLE/4] && (regs[VIDEO_STATUS/4]&16u));
        regs[VIDEO_MODE/4]=value&255u;regs[VIDEO_FORMAT/4]=(value>>8)&3u;
        regs[VIDEO_MODE_STATUS/4]=1;delay=3;
    } else if(off==VIDEO_IRQ_STATUS)regs[off/4]&=~value;
    else regs[off/4]=value;
}
uint32_t ae350_video_test_cycle(void) {ticks+=10000000u;return ticks;}
int ae350_video_test_clean(uint32_t base,uint32_t bytes) {
    assert(!(base&31u) && !(bytes&31u));
    clean_calls++;clean_base=base;clean_bytes=bytes;return clean_error;
}

static uint32_t checksum(const uint16_t *fb) {
    uint32_t hash=2166136261u;
    for(unsigned i=0;i<640u*480u;i++)hash=(hash^fb[i])*16777619u;
    return hash;
}

int main(int argc,char **argv) {
    uint16_t *a=(uint16_t *)0x01000000u,*b=(uint16_t *)0x01100000u;
    unsigned bytes=640u*480u*2u;
    regs[0]=AE350_VIDEO_ID;regs[1]=AE350_ABI_VERSION;regs[2]=15;
    regs[VIDEO_GEOMETRY/4]=640u|(480u<<16);regs[VIDEO_PIXEL_HZ/4]=25000000;
    regs[VIDEO_STATUS/4]=16;
    regs[1]=0x00020000u;
    assert(ae350_video_init(a,b,1280)==AE350_VIDEO_DEVICE);
    regs[1]=AE350_ABI_VERSION;regs[0]^=1;
    assert(ae350_video_init(a,b,1280)==AE350_VIDEO_DEVICE);
    regs[0]=AE350_VIDEO_ID;regs[2]=7;
    assert(ae350_video_init(a,b,1280)==AE350_VIDEO_DEVICE);
    regs[2]=15;assert(!commit_calls);
    assert(ae350_video_init(a,(uint16_t *)((uintptr_t)a+32),1280)==AE350_VIDEO_RANGE);
    assert(ae350_video_init((uint16_t *)((uintptr_t)a+1),b,1280)==AE350_VIDEO_ALIGN);
    assert(ae350_video_init(a,b,1281)==AE350_VIDEO_ALIGN);
    assert(ae350_video_init((uint16_t *)0x80000u,b,1280)==AE350_VIDEO_RANGE);
    assert(ae350_video_init(a,b,0)==0);
    assert(ae350_video_frame(0)==a && ae350_video_frame(1)==b);
    assert(ae350_video_present(0)==0 && clean_base==(uintptr_t)a && clean_bytes==bytes);
    assert(ae350_video_frame(0)==0 && ae350_video_frame(1)==b);
    unsigned before=clean_calls;
    assert(ae350_video_present(0)==0 && clean_calls==before);
    assert(ae350_video_present(1)==0 && clean_base==(uintptr_t)b);
    assert(ae350_video_frame(1)==0 && ae350_video_frame(0)==a);
    assert(ae350_video_present(2)==AE350_VIDEO_STATE);
    clean_error=1;before=commit_calls;
    assert(ae350_video_present(0)==AE350_VIDEO_CACHE && commit_calls==before);clean_error=0;
    reject=1;assert(ae350_video_present(0)==AE350_VIDEO_BUSY);reject=0;
    frozen=1;regs[VIDEO_COMMIT/4]=1;ticks=0xfffffff0u;before=clean_calls;
    assert(!ae350_video_frame(0) && !ae350_video_frame(1));
    assert(ae350_video_present(0)==AE350_VIDEO_TIMEOUT && clean_calls==before);
    frozen=0;regs[VIDEO_COMMIT/4]=0;
    hold_drain=1;assert(ae350_video_stop()==AE350_VIDEO_TIMEOUT);
    assert(!ae350_video_frame(0) && !ae350_video_frame(1));
    hold_drain=0;assert(ae350_video_stop()==0);
    assert(ae350_video_frame(0)==a && ae350_video_frame(1)==b);
    assert(ae350_video_status(0)==AE350_VIDEO_DEVICE);
    struct ae350_video_status status;
    assert(ae350_video_status(&status)==0 && status.width==640 && status.height==480 && status.stride==1280);

    assert(mmap(a,bytes,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS|MAP_FIXED_NOREPLACE,-1,0)==a);
    assert(mmap(b,bytes,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS|MAP_FIXED_NOREPLACE,-1,0)==b);
    memset(a,0x5a,bytes);memset(b,0xa5,bytes);
    static struct ae350_tty tty;
    ae350_tty_init(&tty);ae350_tty_write(&tty,"\033[2J\033[H\033[31mRED");
    assert(ae350_tty_flush(&tty)==0 && a[641]==vt_rgb(1));
    uint32_t old=checksum(a);
    ae350_tty_write(&tty,"\033[2;5H\033[32mZ");
    assert(ae350_tty_flush(&tty)==0 && checksum(a)==old && b[17u*640u+33u]==vt_rgb(2));
    assert(tty.terminal.grid[1][4].ch=='Z');
    ae350_tty_write(&tty,"\033[2J\033[H");
    for(unsigned i=0;i<31;i++)ae350_tty_write(&tty,"line\r\n");
    assert(tty.terminal.cy==VT_ROWS-1 && ae350_tty_flush(&tty)==0);

    // The same BIOS VT parser, glyphs and demo text generate the capture fixture.
    assert(ae350_video_stop()==0);ae350_tty_init(&tty);
    ae350_tty_write(&tty,ae350_hdmi_console_text);assert(ae350_tty_flush(&tty)==0);
    if(argc==2) {
        FILE *f=fopen(argv[1],"wb");assert(f);
        assert(fwrite(a,1,bytes,f)==bytes);assert(!fclose(f));
    }
    assert(!munmap(a,bytes) && !munmap(b,bytes));
    // Runtime API: mode changes drain, disable output and invalidate buffers.
    regs[2]=AE350_VIDEO_CAP;regs[VIDEO_ENABLE/4]=1;regs[VIDEO_MODE_COUNT/4]=1;
    unsigned connected=99;
    assert(ae350_video_get_hpd(0)==AE350_VIDEO_RANGE);
    regs[VIDEO_HPD/4]=0;assert(!ae350_video_get_hpd(&connected) && !connected);
    regs[VIDEO_HPD/4]=2;assert(!ae350_video_get_hpd(&connected) && !connected);
    regs[VIDEO_HPD/4]=3;assert(!ae350_video_get_hpd(&connected) && connected);
    regs[VIDEO_HPD/4]=1;assert(!ae350_video_get_hpd(&connected) && !connected);
    regs[2]&=~0x80u;assert(ae350_video_get_hpd(&connected)==AE350_VIDEO_DEVICE);
    regs[2]=AE350_VIDEO_CAP;
    regs[VIDEO_MODE_INFO_BASE/4]=640u|(480u<<16);
    regs[VIDEO_MODE_INFO_BASE/4+1]=25178571u;
    regs[VIDEO_MODE_INFO_BASE/4+2]=800u|(525u<<16);
    regs[VIDEO_MODE_INFO_BASE/4+6]=59949u;
    regs[VIDEO_MODE_INFO_BASE/4+7]=25175000u;
    unsigned events;
    regs[VIDEO_IRQ_STATUS/4]=3;
    assert(ae350_video_hpd_events(0)==AE350_VIDEO_RANGE);
    assert(!ae350_video_hpd_events(&events) && events==3);
    assert(!ae350_video_hpd_irq_enable(3) && regs[VIDEO_IRQ_ENABLE/4]==3);
    assert(ae350_video_hpd_irq_enable(4)==AE350_VIDEO_RANGE);
    assert(ae350_video_hpd_ack(4)==AE350_VIDEO_RANGE);
    assert(!ae350_video_hpd_ack(1));
    assert(!ae350_video_hpd_events(&events) && events==2);
    assert(!ae350_video_hpd_irq_enable(0));
    regs[2]&=~0x100u;
    assert(ae350_video_hpd_irq_enable(1)==AE350_VIDEO_DEVICE);
    assert(ae350_video_hpd_ack(1)==AE350_VIDEO_DEVICE);
    assert(ae350_video_hpd_events(&events)==AE350_VIDEO_DEVICE);
    regs[2]=AE350_VIDEO_CAP;
    struct ae350_video_mode mode;
    assert(ae350_video_mode_count()==1);
    assert(ae350_video_get_mode(1,&mode)==AE350_VIDEO_RANGE);
    assert(ae350_video_get_mode(0,&mode)==0 && mode.width==640 && mode.v_total==525);
    assert(ae350_video_set_mode(1,AE350_VIDEO_RGB565)==AE350_VIDEO_RANGE);
    assert(ae350_video_set_mode(0,(enum ae350_video_format)3)==AE350_VIDEO_RANGE);
    assert(ae350_video_set_mode(0,AE350_VIDEO_RGB888)==0);
    assert(!regs[VIDEO_ENABLE/4] && !ae350_video_frame_data(0));
    void *large_a=(void *)0x02000000u,*large_b=(void *)0x02400000u;
    assert(ae350_video_init(a,b,0)==AE350_VIDEO_STATE);
    assert(ae350_video_init_buffers(large_a,large_b,1280)==AE350_VIDEO_ALIGN);
    assert(ae350_video_init_buffers(large_a,large_b,0)==0);
    assert(ae350_video_frame_data(0)==large_a && !ae350_video_frame(0));
    assert(ae350_video_present(0)==0 && clean_bytes==640u*480u*3u);
    assert(regs[VIDEO_ENABLE/4]==1);
    assert(ae350_video_set_mode(0,AE350_VIDEO_XRGB8888)==0);
    assert(ae350_video_init_buffers(large_a,large_b,0)==0);
    assert(ae350_video_present(1)==0 && clean_bytes==640u*480u*4u);
    assert(ae350_video_status(&status)==0 && status.format==AE350_VIDEO_XRGB8888 && status.output_enabled);
    mode_frozen=1;
    assert(ae350_video_set_mode(0,AE350_VIDEO_RGB565)==AE350_VIDEO_TIMEOUT);
    assert(!ae350_video_frame_data(0) && !ae350_video_frame_data(1));
    mode_frozen=0;regs[VIDEO_MODE_STATUS/4]=0;
    puts("PASS video HAL ownership, cache order, bounds, timeout/drain and BIOS TTY rendering");
    return 0;
}
