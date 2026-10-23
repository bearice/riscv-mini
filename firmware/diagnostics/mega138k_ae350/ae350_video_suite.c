/* RGB565 ownership/flip/stop suite. Host reads results only after UART DONE. */
#include <stdint.h>
#include "ae350_address_map.h"
#include "ae350_video_fixture.h"
#ifndef AE350_VIDEO_SECONDS
#define AE350_VIDEO_SECONDS 10u
#endif
#define W(a) (*(volatile uint32_t *)(uintptr_t)(a))
static volatile uint32_t *const out=(volatile uint32_t *)AE350_RESULTS_BASE;
static void puts_uart(const char *s) {
    while(*s){while(!(W(0xf0300034u)&0x20u)){}W(0xf0300020u)=(uint8_t)*s++;}
}
static void finish(unsigned error) {
    struct ae350_video_status s;
    out[1]=error;
    if(!ae350_video_status(&s)) {
        out[8]=s.frames;out[9]=s.underflows;out[10]=s.missing;out[11]=s.address_error;
        out[12]=s.timeout_error;out[13]=s.errors;out[14]=s.fifo_min;out[15]=s.max_wait;
        out[16]=s.reads;out[17]=s.cpu_commands;out[18]=s.request_overflow;
        out[19]=s.active_base;out[20]=s.width;out[21]=s.height;out[22]=s.stride;
        out[7]=s.enabled;
    }
    out[0]=0xc6100001u;__asm__ volatile("fence":::"memory");
    puts_uart("AE350 VIDEO DONE\r\n");
    for(;;)__asm__ volatile("wfi");
}
int main(void) {
    for(unsigned i=0;i<32;i++)out[i]=0;
    out[31]=0x56490001u;out[0]=1;
    __asm__ volatile("csrw 0x7cc,%0\n\tcsrs 0x7ca,%1"::"r"(23),"r"(2):"memory");
    int error=video_fixture_init();if(error)finish(0x100u|(unsigned)error);
    puts_uart("AE350 VIDEO READY\r\n");out[0]=2;
    for(unsigned i=0;i<100;i++) {
        if(ae350_video_present((i+1u)&1u))finish(2);
        if(ae350_video_frame((i+1u)&1u))finish(3); /* front buffer is owned by reader */
        out[2]++;
    }
    if(ae350_video_present(2)!=AE350_VIDEO_STATE)finish(4);
    if(ae350_video_init((uint16_t *)(VIDEO_FB0+1u),(uint16_t *)VIDEO_FB1,1280)!=AE350_VIDEO_ALIGN)finish(5);
    if(ae350_video_init((uint16_t *)VIDEO_FB0,(uint16_t *)(VIDEO_FB0+32u),1280)!=AE350_VIDEO_RANGE)finish(6);
    out[4]=3;
    for(unsigned i=0;i<100;i++) {
        if(ae350_video_stop())finish(7);
        if(!ae350_video_frame(0) || !ae350_video_frame(1))finish(8);
        if(ae350_video_present(i&1u))finish(9);
        out[3]++;
    }
    out[0]=3;
    volatile uint32_t *stress=(volatile uint32_t *)0x00300000u;
    uint32_t baseline=W(AE350_VIDEO_BASE+VIDEO_FRAMES);
    while(W(AE350_VIDEO_BASE+VIDEO_FRAMES)-baseline<AE350_VIDEO_SECONDS*60u) {
        for(unsigned i=0;i<16384;i++)stress[i]=i^out[5];
        if(ae350_dma_sync_for_device((uintptr_t)stress,65536))finish(10);
        for(unsigned i=0;i<16384;i++)if(stress[i]!=(i^out[5]))finish(11);
        out[5]++;
    }
    if(video_fixture_guards())finish(12);
    out[6]=1;
    struct ae350_video_status s;if(ae350_video_status(&s))finish(13);
    if(s.underflows || s.missing || s.address_error || s.timeout_error || s.errors || s.request_overflow)finish(14);
    if(!s.reads || !s.cpu_commands || s.fifo_min>2048)finish(15);
    finish(0);return 0;
}
