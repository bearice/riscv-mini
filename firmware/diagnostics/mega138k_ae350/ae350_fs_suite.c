/* File-only write acceptance. Create a fresh directory; never format/raw-write. */
#include "ae350_bios.h"
#include "ae350_address_map.h"
#include "ae350_sd.h"
#include "board_management.h"
#include "ae350_video.h"
#include "ae350_audio.h"
#include "ff.h"
#include <string.h>
static volatile uint32_t *const r=(volatile uint32_t *)AE350_RESULTS_BASE;
static uint8_t data[4128];
static _Alignas(32) uint32_t ring[2048],pcm[1024];
static char directory[16]="/P7T000",name[32];
static uint32_t crc_step(uint32_t c,const uint8_t *p,unsigned n) {
    while(n--) {c^=*p++;for(unsigned i=0;i<8;i++)c=(c>>1)^(0xedb88320u&-(c&1u));}return c;
}
static uint8_t pattern(unsigned p) {return (uint8_t)((p*37u)^(p>>8)^(p>>17)^0xa5u);}
static void finish(unsigned error) {
    r[1]=error;r[2]=bios_unmount();
    r[7]=board_management_stop();r[8]=ae350_video_stop();r[9]=audio_tx_stop();
    struct ae350_sd_info info;ae350_sd_info(&info);r[3]=info.hz;r[4]=info.error;
    __asm__ volatile("fence":::"memory");r[0]=0x5037ffffu;
    bios_puts("AE350 FS DONE\r\n");for(;;)__asm__ volatile("wfi");
}
void main(void) {
    for(unsigned i=0;i<96;i++)r[i]=0;
    r[0]=0x50370001u;bios_runtime_init();
    struct bios_info info;
    if(bios_call(BIOS_INFO,(uintptr_t)&info,0,0,0) || info.version!=1 || info.width!=640 ||
       bios_call(999,0,0,0,0)!=-1 || bios_call(BIOS_FS_READ,99,(uintptr_t)data,1,0)!=-1 ||
       bios_call(BIOS_INFO,0xf8000000u,0,0,0)!=-1 || bios_call(BIOS_VIDEO_MODE,2,0,0,0)!=-1)finish(1);
    r[5]=1;
    bios_poll(); /* Render the first TTY frame before starting real-time audio. */
    for(unsigned i=0;i<1024;i++)pcm[i]=0x01000100u;
    if(bios_call(BIOS_AUDIO_BEGIN,(uintptr_t)ring,2048,0,0) ||
       bios_call(BIOS_AUDIO_WRITE,(uintptr_t)pcm,1024,0,0)!=1024 ||
       bios_call(BIOS_AUDIO_CONTROL,BIOS_AUDIO_PLAY,0,0,0))finish(121);
    unsigned deadline=bios_time_ms()+1000;
    struct bios_audio audio;
    do {
        bios_poll();
        if(bios_call(BIOS_AUDIO_INFO,(uintptr_t)&audio,0,0,0))finish(122);
        if((int)(bios_time_ms()-deadline)>=0)finish(123);
    }while(audio.played<256);
    if(bios_call(BIOS_AUDIO_CONTROL,BIOS_AUDIO_PAUSE,0,0,0) ||
       audio.sample_rate!=48000 || audio.underruns || audio.errors ||
       bios_call(BIOS_AUDIO_CONTROL,BIOS_AUDIO_STOP,0,0,0) ||
       bios_call(BIOS_AUDIO_WRITE,(uintptr_t)pcm,1,0,0)!=-1 ||
       bios_call(BIOS_AUDIO_CONTROL,99,0,0,0)!=-1)finish(124);
    r[11]=1;
    if(bios_mount())finish(2);
    unsigned index;
    for(index=0;index<1000;index++) {
        directory[4]='0'+index/100;directory[5]='0'+(index/10)%10;directory[6]='0'+index%10;
        FRESULT rc=f_mkdir(directory);if(rc==FR_OK)break;if(rc!=FR_EXIST)finish(3);
    }
    if(index==1000)finish(4);
    r[6]=index;
    for(unsigned test=0;test<3;test++) {
        unsigned bytes=(test==0?1u:test==1?16u:64u)<<20;
        memcpy(name,directory,7);memcpy(name+7,"/TEST0.BIN",11);name[12]='0'+test;
        int h=bios_fs_open(name,BIOS_FS_O_WRITE|BIOS_FS_O_CREATE);if(h<0)finish(10+test);
        uint32_t crc=~0u;unsigned start=bios_time_ms();
        for(unsigned offset=0;offset<bytes;) {
            unsigned n=bytes-offset;if(n>4093)n=4093;
            data[0]=data[n+1]=0xa5;
            for(unsigned i=0;i<n;i++)data[i+1]=pattern(offset+i);
            crc=crc_step(crc,data+1,n);
            if(bios_fs_write(h,data+1,n)!=(int)n || data[0]!=0xa5 || data[n+1]!=0xa5)finish(20+test);
            offset+=n;bios_poll();
        }
        r[16+test*8]=~crc;r[18+test*8]=bios_time_ms()-start;
        if(bios_fs_close(h) || bios_unmount() || bios_mount())finish(30+test);
        struct bios_fs_stat st;if(bios_fs_stat(name,&st) || st.size!=bytes)finish(40+test);
        h=bios_fs_open(name,BIOS_FS_O_READ);if(h<0)finish(50+test);
        if(bios_fs_write(h,data,1)!=-1 || bios_unmount()!=-1)finish(60+test);
        crc=~0u;start=bios_time_ms();
        for(unsigned offset=0;offset<bytes;) {
            unsigned n=bytes-offset;if(n>4091)n=4091;
            data[0]=data[n+1]=0xa5;
            if(bios_fs_read(h,data+1,n)!=(int)n || data[0]!=0xa5 || data[n+1]!=0xa5)finish(70+test);
            for(unsigned i=0;i<n;i++)if(data[i+1]!=pattern(offset+i))finish(80+test);
            crc=crc_step(crc,data+1,n);offset+=n;bios_poll();
        }
        r[17+test*8]=~crc;r[19+test*8]=bios_time_ms()-start;
        if(r[16+test*8]!=~crc || bios_fs_read(h,data,1)!=0 ||
           bios_fs_seek(h,-17,BIOS_FS_END)!=(int)(bytes-17) || bios_fs_read(h,data,17)!=17)finish(90+test);
        for(unsigned i=0;i<17;i++)if(data[i]!=pattern(bytes-17+i))finish(100+test);
        if(bios_fs_close(h) || bios_fs_close(h)!=-1)finish(110+test);
        r[20+test*8]=bytes;r[21+test*8]=1;
        bios_puts("AE350 FS FILE PASS\r\n");
    }
    memcpy(name,directory,7);memcpy(name+7,"/CONSOLE.TXT",13);
    static const char text[]="AE350 P7 filesystem\r\n";
    int h=bios_fs_open(name,BIOS_FS_O_WRITE|BIOS_FS_O_CREATE);
    if(h<0 || bios_fs_write(h,text,sizeof(text)-1)!=(int)(sizeof(text)-1) || bios_fs_close(h))finish(120);
    r[10]=1;
    memcpy(name+7,"/PROGRAM.BAS",13);
    static const char program[]="10 PRINT \"AE350 FILE BASIC\"\r\n20 END\r\n";
    h=bios_fs_open(name,BIOS_FS_O_WRITE|BIOS_FS_O_CREATE);
    if(h<0 || bios_fs_write(h,program,sizeof(program)-1)!=(int)(sizeof(program)-1) || bios_fs_close(h))finish(125);
    r[12]=1;
    finish(0);
}
