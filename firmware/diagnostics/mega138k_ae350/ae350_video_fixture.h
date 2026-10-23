#pragma once
#include "ae350_video.h"
#include "ae350_dma.h"

#define VIDEO_FB0 0x01000000u
#define VIDEO_FB1 0x01100000u
#define VIDEO_FB_BYTES (640u*480u*2u)
#define VIDEO_GUARD 0x5650c35au

__attribute__((always_inline))
static inline uint16_t video_fixture_pixel(unsigned x,unsigned y,unsigned slot) {
    unsigned r=32u+slot*16u,g=r,b=r;
    if((x&31u)==31u || (y&31u)==31u)r=g=b=0;
    if(x>=96 && x<288) {
        if(y>=64 && y<160){r=63;g=b=0;}
        if(y>=192 && y<288){g=63;r=b=0;}
        if(y>=320 && y<416){b=63;r=g=0;}
    }
    if(x>=352 && x<544) {
        if(y>=64 && y<160){r=0;g=b=63;}
        if(y>=192 && y<288){g=0;r=b=63;}
        if(y>=320 && y<416){b=0;r=g=63;}
    }
    if(!x || x==639 || !y || y==479)r=g=b=255;
    return (uint16_t)(((r>>3)<<11)|((g>>2)<<5)|(b>>3));
}

__attribute__((section(".ilm.text"),noinline))
static int video_fixture_init(void) {
    int error=ae350_video_init((uint16_t *)VIDEO_FB0,(uint16_t *)VIDEO_FB1,1280);
    if(error)return error;
    for(unsigned slot=0;slot<2;slot++) {
        uint16_t *fb=ae350_video_frame(slot);
        if(!fb)return 100;
        for(unsigned y=0;y<480;y++)for(unsigned x=0;x<640;x++)
            fb[y*640u+x]=video_fixture_pixel(x,y,slot);
        if(ae350_dma_sync_for_device((uintptr_t)fb,VIDEO_FB_BYTES) ||
           ae350_dma_sync_for_cpu((uintptr_t)fb,VIDEO_FB_BYTES))return 101;
        for(unsigned y=0;y<480;y++)for(unsigned x=0;x<640;x++)
            if(fb[y*640u+x]!=video_fixture_pixel(x,y,slot))return 102;
        volatile uint32_t *lo=(volatile uint32_t *)((uintptr_t)fb-32u);
        volatile uint32_t *hi=(volatile uint32_t *)((uintptr_t)fb+VIDEO_FB_BYTES);
        for(unsigned i=0;i<8;i++)lo[i]=hi[i]=VIDEO_GUARD;
        if(ae350_dma_sync_for_device((uintptr_t)lo,32) || ae350_dma_sync_for_device((uintptr_t)hi,32))return 103;
    }
    return ae350_video_present(0);
}

static int video_fixture_guards(void) {
    for(unsigned slot=0;slot<2;slot++) {
        uintptr_t base=slot?VIDEO_FB1:VIDEO_FB0;
        if(ae350_dma_sync_for_cpu(base-32u,32) ||
           ae350_dma_sync_for_cpu(base+VIDEO_FB_BYTES,32))return 1;
        for(unsigned i=0;i<8;i++)
            if(((volatile uint32_t *)(base-32u))[i]!=VIDEO_GUARD ||
               ((volatile uint32_t *)(base+VIDEO_FB_BYTES))[i]!=VIDEO_GUARD)return 1;
    }
    return 0;
}
