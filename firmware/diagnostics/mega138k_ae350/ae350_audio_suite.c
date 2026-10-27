/* Real-time audio suite: no debugger polling while either FIFO is active.
 * The host waits for the final UART marker before it halts to read results.
 * Acoustic stimulus and subjective listening remain separate observations. */
#include <stdint.h>
#include "ae350_audio.h"
#include "ae350_address_map.h"
#ifdef AE350_VIDEO_STRESS
#include "ae350_video_fixture.h"
#endif
#ifdef AE350_SD_STRESS
#include "ae350_bios.h"
#include "ae350_sd.h"
#ifndef AE350_SD_STRESS_PATH
#define AE350_SD_STRESS_PATH "/P7T000/TEST0.BIN"
#endif
static int sd_file=-1;
static uint8_t sd_data[4098];
static uint32_t sd_crc=~0u,sd_offset,last_sd_frame;
static int sd_step(void) {
    sd_data[0]=sd_data[4097]=0xa5;
    if(bios_fs_read(sd_file,sd_data+1,4096)!=4096)return 1;
    for(unsigned i=0;i<4096;i++) {
        unsigned p=sd_offset+i;
        if(sd_data[i+1]!=(uint8_t)((p*37u)^(p>>8)^(p>>17)^0xa5u))return 2;
        sd_crc^=sd_data[i+1];
        for(unsigned bit=0;bit<8;bit++)sd_crc=(sd_crc>>1)^(0xedb88320u&-(sd_crc&1u));
    }
    if(sd_data[0]!=0xa5 || sd_data[4097]!=0xa5)return 3;
    sd_offset+=4096;
    volatile uint32_t *r=(volatile uint32_t *)AE350_RESULTS_BASE;
    r[129]++;
    if(sd_offset==(1u<<20)) {
        r[131]=~sd_crc;if(~sd_crc!=0x3d68bdccu)return 4;
        r[130]++;sd_crc=~0u;sd_offset=0;
        if(bios_fs_seek(sd_file,0,BIOS_FS_SET))return 5;
    }
    return 0;
}
#endif
#ifndef AE350_AUDIO_SECONDS
#define AE350_AUDIO_SECONDS 5u
#endif
#define W(a) (*(volatile uint32_t *)(uintptr_t)(a))
#define TXBUF ((uint32_t *)0x00100000u)
#define RXBUF ((int32_t *)0x00200000u)
#define CPU_BUF ((volatile uint32_t *)0x00300000u)
#define BLOCK 1536u
#define GUARD 0x51a0c35au
#define DONE 0xc5100001u
#define SCHEMA 0x41550001u
#define PCM_SCHEMA 0x50434d01u
static volatile uint32_t *const out=(volatile uint32_t *)AE350_RESULTS_BASE;
static uint32_t last_dma_result;
enum {STAGE,ERROR,PIO,PIO_GAPS,RESTARTS,SNAPSHOT,MINIMUM,MAXIMUM,ACTIVITY,
      OVERFLOW,BUSY_REJECT,HW_ERROR,FRAMES,PLAYED,UNDERRUNS,RX_OVERRUNS,
      TX_BLOCKS,RX_BLOCKS,RX_BAD_SIGN,GUARDS,CPU_WRITES,SAMPLE_RATE,
      ACCEPTED,PHYSICAL,SECONDS,MIN_TX_LEVEL,MAX_RX_LEVEL,R27,R28,R29,R30,VERSION};
static const int16_t sine[32]={0,200,392,569,724,851,946,1004,1024,1004,946,851,724,569,392,200,
    0,-200,-392,-569,-724,-851,-946,-1004,-1024,-1004,-946,-851,-724,-569,-392,-200};
static void putc(char c) {
    while (!(W(0xf0300034u)&0x20u)) {}
    W(0xf0300020u)=(uint8_t)c;
}
static void puts(const char *s) {while (*s) putc(*s++);}
static void hex(uint32_t word) {
    for(int shift=28;shift>=0;shift-=4)putc("0123456789abcdef"[(word>>shift)&15u]);
}
static uint32_t cycles(void) {uint32_t c;__asm__ volatile("rdcycle %0":"=r"(c));return c;}
static int expired(uint32_t start) {return (uint32_t)(cycles()-start)>3200000000u;}
static int wait_frames(uint32_t frames) {
    uint32_t start=cycles();
    while (audio_tx_info(AUDIO_FRAMES)<frames) if (expired(start)) return 0;
    return 1;
}
__attribute__((section(".ilm.text"), noinline))
static uint32_t observe_capture(uint32_t peak) {
    uint32_t maximum=out[R28],bad=0;
    for(unsigned i=0;i<BLOCK;i++) {
        int32_t sample=RXBUF[i];
        if(sample!=((int32_t)((uint32_t)sample<<8)>>8))bad++;
        uint32_t magnitude=sample<0?0u-(uint32_t)sample:(uint32_t)sample;
        if(magnitude>peak)peak=magnitude;
        if(magnitude>maximum)maximum=magnitude;
    }
    out[RX_BAD_SIGN]+=bad;out[R28]=maximum;
    return peak;
}
static void finish(unsigned error) {
    out[ERROR]=error;
    if(error && error!=34u) {
        out[32]=W(AE350_DMAC_BASE+0x40);out[33]=W(AE350_DMAC_BASE+0x44);
        out[34]=W(AE350_DMAC_BASE+0x60);out[35]=W(AE350_DMAC_BASE+0x64);
        out[36]=W(AE350_DMAC_BASE+0x30);out[37]=audio_tx_info(AUDIO_LEVEL);
        out[38]=mic_rx_info(AUDIO_LEVEL);out[39]=last_dma_result;
        if(out[STAGE]==4u && !out[FRAMES]) {
            out[FRAMES]=audio_tx_info(AUDIO_FRAMES);out[PLAYED]=audio_tx_info(AUDIO_PLAYED);
            out[UNDERRUNS]=audio_tx_info(AUDIO_UNDERRUN);out[RX_OVERRUNS]=mic_rx_info(MIC_OVERRUN);
            out[ACCEPTED]=mic_rx_info(MIC_ACCEPTED);
        }
    }
    int tx_rc=audio_tx_stop(), rx_rc=mic_rx_stop();
    if (!error && (tx_rc||rx_rc)) out[ERROR]=0x100u|(uint32_t)(tx_rc?tx_rc:rx_rc);
#ifdef AE350_SD_STRESS
    if(sd_file>=0) {out[134]=bios_fs_close(sd_file);sd_file=-1;}
    out[135]=bios_unmount();
    struct ae350_sd_info sd;ae350_sd_info(&sd);
    out[132]=sd.hz;out[133]=sd.error;
    if(!out[ERROR] && (out[128]!=0x53445301u || !out[130] || out[131]!=0x3d68bdccu ||
       out[132]!=25000000 || out[133] || out[134] || out[135]))out[ERROR]=0x301u;
#endif
#ifdef AE350_VIDEO_STRESS
    struct ae350_video_status video;
    if(ae350_video_status(&video))out[97]=1;
    else {
        out[98]=video.width;out[99]=video.height;out[100]=video.frames;
        out[101]=video.underflows;out[102]=video.missing;out[103]=video.address_error;
        out[104]=video.timeout_error;out[105]=video.errors;out[106]=video.fifo_min;
        out[107]=video.max_wait;out[108]=video.reads;out[109]=video.cpu_commands;
        out[110]=video.request_overflow;out[112]=video.active_base;
        out[113]=video.enabled;out[114]=video.pending;
        out[111]=out[97]?1u:(unsigned)video_fixture_guards();
        if(video.underflows || video.missing || video.address_error || video.timeout_error ||
           video.errors || video.request_overflow || out[111])out[97]=2;
    }
    if(out[97] && !out[ERROR])out[ERROR]=0x200u|out[97];
#endif
    out[STAGE]=DONE;
    __asm__ volatile("fence":::"memory");
    puts("AE350 AUDIO DONE\r\n");
    for (;;) __asm__ volatile("nop");
}
int main(void) {
    for (unsigned i=0;i<80;i++) out[i]=0;
    out[64]=PCM_SCHEMA;
    out[VERSION]=SCHEMA; out[SECONDS]=AE350_AUDIO_SECONDS;
    ae350_dma_reset(); /* this standalone suite owns all DMA channels */
#ifdef AE350_SD_STRESS
    /* Match the BIOS execution mode: FatFs and CRC must fit the refill budget. */
    __asm__ volatile("csrw 0x7cc, %0\n\tcsrs 0x7ca, %1"::"r"(23),"r"(3):"memory");
#else
    __asm__ volatile("csrw 0x7cc, %0\n\tcsrs 0x7ca, %1"::"r"(23),"r"(2):"memory");
#endif
    uint32_t cache_control;
    __asm__ volatile("csrr %0, 0x7ca":"=r"(cache_control));
    out[R27]=cache_control;
#ifdef AE350_SD_STRESS
    for(unsigned i=128;i<144;i++)out[i]=0;
    out[128]=0x53445301u;
    sd_file=bios_fs_open(AE350_SD_STRESS_PATH,BIOS_FS_O_READ);
    if(sd_file<0 || bios_file_size(AE350_SD_STRESS_PATH)!=(1<<20))finish(0x300u);
#endif
#ifdef AE350_VIDEO_STRESS
    for(unsigned i=96;i<128;i++)out[i]=0;
    out[96]=0x56490002u;out[97]=(unsigned)video_fixture_init();
    if(out[97])finish(0x200u|out[97]);
#endif
    if (AE350_AUDIO_SECONDS<5u || AE350_AUDIO_SECONDS>3600u) finish(1);
    if (audio_tx_init() || mic_rx_init(0)) finish(2);
    out[SAMPLE_RATE]=audio_tx_info(AUDIO_RATE);
    for (unsigned i=0;i<BLOCK;i++)
        TXBUF[i]=(uint16_t)sine[(i/2u)&31u]|((uint32_t)(uint16_t)sine[(i/3u)&31u]<<16);
    out[69]=TXBUF[3];out[70]=TXBUF[24];
    if(ae350_dma_sync_for_device((uintptr_t)TXBUF,BLOCK*4u))finish(41);
    W((uintptr_t)TXBUF-4)=W((uintptr_t)TXBUF+BLOCK*4u)=GUARD;
    W((uintptr_t)RXBUF-4)=W((uintptr_t)RXBUF+BLOCK*4u)=GUARD;
    if(ae350_dma_sync_for_device((uintptr_t)TXBUF-32,32)||
       ae350_dma_sync_for_device((uintptr_t)TXBUF+BLOCK*4u,32)||
       ae350_dma_sync_for_device((uintptr_t)RXBUF-32,32)||
       ae350_dma_sync_for_device((uintptr_t)RXBUF+BLOCK*4u,32))finish(32);
    out[STAGE]=1;
    unsigned n=0;
    if (audio_tx_write(TXBUF,256,&n)||n!=256||audio_tx_start(1)||!wait_frames(320)) finish(3);
    out[PIO]=audio_tx_info(AUDIO_PLAYED);out[PIO_GAPS]=audio_tx_info(AUDIO_UNDERRUN);
    if (out[PIO]!=256||out[PIO_GAPS]<1||audio_tx_stop()) finish(4);
    for (unsigned i=0;i<100;i++) {
        if (audio_tx_write(TXBUF,8,&n)||n!=8||audio_tx_start(1)||!wait_frames(8)||audio_tx_stop()) finish(5);
        if (audio_tx_info(AUDIO_LEVEL)||audio_tx_info(AUDIO_LAST)) finish(6);
        out[RESTARTS]++;
    }
    out[STAGE]=2;
    uint32_t start=cycles();
    while (mic_rx_info(MIC_SAMPLES)<9600u) if (expired(start)) finish(7);
    if (mic_rx_capture()) finish(8);
    start=cycles();
    while (!(mic_rx_info(0x10)&2u)) if (expired(start)) finish(9);
    out[SNAPSHOT]=mic_rx_info(MIC_CAPTURED);out[ACTIVITY]=mic_rx_info(0x3c);
    int32_t minimum=0x7fffffff,maximum=(int32_t)0x80000000u;
    if (mic_rx_read(RXBUF,512,&n)||n!=512) finish(10);
    for (unsigned i=0;i<n;i++) {if(RXBUF[i]<minimum)minimum=RXBUF[i];if(RXBUF[i]>maximum)maximum=RXBUF[i];}
    out[MINIMUM]=(uint32_t)minimum;out[MAXIMUM]=(uint32_t)maximum;
    if (out[SNAPSHOT]!=512) finish(11);
    if(ae350_dma_sync_for_device((uintptr_t)RXBUF,BLOCK*4u))finish(42);
    if (mic_rx_stream()) finish(12);
    start=cycles();
    uint32_t target=mic_rx_info(MIC_SAMPLES)+1024u;
    while (mic_rx_info(MIC_SAMPLES)<target) if (expired(start)) finish(13);
    out[OVERFLOW]=mic_rx_info(MIC_OVERRUN);
    if (!out[OVERFLOW]||mic_rx_stop()) finish(14);

    out[STAGE]=3;
#ifdef AE350_AUDIO_ROUTE_SCAN
    /* Qualify the controller selector independently of the exported wire.
       Only our TX FIFO can be written. An internal always-ready request is
       excluded by testing the same selector with the external gate closed. */
    out[R27]=out[R28]=0;
    for(unsigned selector=0;selector<16;selector++) {
        for(unsigned enabled=0;enabled<2;enabled++) {
            if(audio_tx_stop())finish(35);
            W(AE350_AUDIO_BASE+0x0c)=2u|(enabled?4u:0u);
            W(AE350_DMAC_BASE+0x30)=0x00010101u;
            W(AE350_DMAC_BASE+0x44)=1;
            W(AE350_DMAC_BASE+0x48)=(uint32_t)(uintptr_t)TXBUF;
            W(AE350_DMAC_BASE+0x4c)=0;W(AE350_DMAC_BASE+0x50)=AE350_AUDIO_DATA;
            W(AE350_DMAC_BASE+0x54)=0;W(AE350_DMAC_BASE+0x58)=0;W(AE350_DMAC_BASE+0x5c)=0;
            __asm__ volatile("fence":::"memory");
            W(AE350_DMAC_BASE+0x40)=(2u<<21)|(2u<<18)|(2u<<12)|(1u<<16)|(selector<<4)|15u;
            for(unsigned guard=0;guard<20000u && ae350_dma_busy(0);guard++){}
            if(audio_tx_info(AUDIO_LEVEL)==1u && ae350_dma_poll(0)==0)
                out[enabled?R27:R28]|=1u<<selector;
            if(ae350_dma_abort_drain(0))finish(36);
            for(volatile unsigned i=0;i<1024;i++){}
        }
    }
    W(AE350_AUDIO_BASE+0xc)=2; /* isolate other external request sources */
    if(mic_rx_init(0)||mic_rx_capture())finish(37);
    start=cycles();while(!(mic_rx_info(0x10)&2u))if(expired(start))finish(38);
    out[R29]=out[R30]=0;
    for(unsigned selector=0;selector<16;selector++) {
        for(unsigned enabled=0;enabled<2;enabled++) {
            W(AE350_MIC_BASE+0xc)=1u|(enabled?8u:0u);
            W(AE350_DMAC_BASE+0x30)=0x00010101u;
            W(AE350_DMAC_BASE+0x44)=1;W(AE350_DMAC_BASE+0x48)=AE350_MIC_DATA;
            W(AE350_DMAC_BASE+0x4c)=0;W(AE350_DMAC_BASE+0x50)=(uint32_t)(uintptr_t)RXBUF;
            W(AE350_DMAC_BASE+0x54)=0;W(AE350_DMAC_BASE+0x58)=0;W(AE350_DMAC_BASE+0x5c)=0;
            __asm__ volatile("fence":::"memory");
            W(AE350_DMAC_BASE+0x40)=(2u<<21)|(2u<<18)|(2u<<14)|(1u<<17)|(selector<<8)|15u;
            for(unsigned guard=0;guard<20000u && ae350_dma_busy(0);guard++){}
            if(ae350_dma_poll(0)==0)out[enabled?R29:R30]|=1u<<selector;
            if(ae350_dma_abort_drain(0))finish(39);
        }
    }
    W(AE350_MIC_BASE+0xc)=1;
    for(unsigned i=32;i<36;i++)out[i]=0;
    for(unsigned direction=0;direction<2;direction++)for(unsigned selector=0;selector<16;selector++) {
        for(unsigned enabled=0;enabled<2;enabled++) {
            W(AE350_DMA_SVC_BASE+0xc)=1;
            if(direction)for(unsigned word=0;word<16;word++)W(AE350_LOOPBACK_DATA)=0x13572468u;
            W(AE350_DMA_SVC_BASE+0xc)=enabled?2u:0u;
            W(AE350_DMAC_BASE+0x30)=0x00010101u;
            W(AE350_DMAC_BASE+0x44)=1;
            W(AE350_DMAC_BASE+0x48)=direction?AE350_LOOPBACK_DATA:(uint32_t)(uintptr_t)TXBUF;
            W(AE350_DMAC_BASE+0x4c)=0;
            W(AE350_DMAC_BASE+0x50)=direction?(uint32_t)(uintptr_t)RXBUF:AE350_LOOPBACK_DATA;
            W(AE350_DMAC_BASE+0x54)=0;W(AE350_DMAC_BASE+0x58)=0;W(AE350_DMAC_BASE+0x5c)=0;
            __asm__ volatile("fence":::"memory");
            W(AE350_DMAC_BASE+0x40)=(2u<<21)|(2u<<18)|15u|
                (direction?((2u<<14)|(1u<<17)|(selector<<8)):((2u<<12)|(1u<<16)|(selector<<4)));
            for(unsigned guard=0;guard<20000u && ae350_dma_busy(0);guard++){}
            if(ae350_dma_poll(0)==0)out[32+direction*2+(enabled?0:1)]|=1u<<selector;
            if(ae350_dma_abort_drain(0))finish(40);
        }
    }
    W(AE350_DMA_SVC_BASE+0xc)=1;
    finish(34);
#endif
    /* With playback paused a block larger than the FIFO must remain busy. */
    if (audio_tx_dma_start(0,TXBUF,BLOCK)) finish(15);
    start=cycles();
    while (audio_tx_info(AUDIO_LEVEL)<256u) if (expired(start)) finish(16);
    struct ae350_dma_req r={.device=AE350_DMAC_ID,.abi=AE350_ABI_VERSION,.csr_base=AE350_DMAC_BASE,
        .irq_id=AE350_IRQ_ID,.need_cap=AE350_CAP_DMA_REQ,.channel=0,
        .src=(uint32_t)(uintptr_t)TXBUF,.dst=AE350_AUDIO_DATA,.bytes=BLOCK*4u,.width_bytes=4,
        .src_mode=0,.dst_mode=2,.handshake=3,.llp=0};
    /* Source/remaining count legitimately advance while the channel runs;
       compare the invariant control and fixed destination instead. */
    uint32_t before=W(AE350_DMAC_BASE+0x40),before_dst=W(AE350_DMAC_BASE+0x50);
    out[BUSY_REJECT]=ae350_dma_submit(&r);
    if (out[BUSY_REJECT]!=AE350_DMA_ERR_BUSY||W(AE350_DMAC_BASE+0x40)!=before||
        W(AE350_DMAC_BASE+0x50)!=before_dst) finish(17);
    int stop_status=audio_tx_stop();if(stop_status)finish(0x1000u+(unsigned)stop_status);
    /* Deliberately bypass the API to provoke one real AHB ERROR, channel2. */
    W(AE350_DMAC_BASE+0x30)=0x00040404u;
    W(AE350_DMAC_BASE+0x84)=1;W(AE350_DMAC_BASE+0x88)=(uint32_t)(uintptr_t)TXBUF;
    W(AE350_DMAC_BASE+0x8c)=0;W(AE350_DMAC_BASE+0x90)=AE350_AUDIO_DATA+4u;
    W(AE350_DMAC_BASE+0x94)=0;W(AE350_DMAC_BASE+0x98)=0;W(AE350_DMAC_BASE+0x9c)=0;
    W(AE350_DMAC_BASE+0x80)=(2u<<21)|(2u<<18)|(2u<<12)|15u;
    start=cycles();
    do {out[HW_ERROR]=ae350_dma_poll(2);if(expired(start))finish(18);} while(out[HW_ERROR]==AE350_DMA_ERR_BUSY);
    if(out[HW_ERROR]!=AE350_DMA_ERR_HW||ae350_dma_abort_drain(2))finish(19);
    for(unsigned i=0;i<100;i++) {
        if(audio_tx_stop()||audio_tx_dma_start(0,TXBUF,BLOCK))finish(43);
        start=cycles();while(audio_tx_info(AUDIO_LEVEL)<16u)if(expired(start))finish(44);
        if(audio_tx_stop()||ae350_dma_busy(0)||audio_tx_info(AUDIO_LEVEL))finish(45);
        uint32_t sentinel=0x01000100u+i, next[64];
        for(unsigned j=0;j<64;j++)next[j]=sentinel;
        if(audio_tx_write(next,64,&n)||n!=64||audio_tx_info(AUDIO_LEVEL)!=64u||
           audio_tx_start(0)||!wait_frames(8))finish(46);
        if(audio_tx_info(AUDIO_PLAYED)<8u||audio_tx_info(AUDIO_LAST)!=sentinel||
           audio_tx_info(AUDIO_UNDERRUN)||audio_tx_stop())finish(47);
        out[R30]++;
    }
    if(audio_tx_stop()||mic_rx_init(0))finish(20);
    start=cycles();while(mic_rx_info(MIC_SAMPLES)<9600u)if(expired(start))finish(7);
    if(mic_rx_stream())finish(20);

    out[STAGE]=4;
    if(audio_tx_dma_start(0,TXBUF,BLOCK)||mic_rx_dma_start(1,RXBUF,BLOCK))finish(21);
    start=cycles();
    while(audio_tx_info(AUDIO_LEVEL)<256u)if(expired(start))finish(22);
#ifdef AE350_VIDEO_STRESS
    out[115]=W(AE350_VIDEO_BASE+VIDEO_FRAMES);
#endif
    puts("AE350 AUDIO STREAM\r\n");
    if(audio_tx_start(0))finish(23);
    uint32_t last_frames=0,last_progress=cycles(),next_tick=480000u,window_peak=0;
    out[R28]=0;out[R29]=AE350_AUDIO_DMA_SEL;
    out[MIN_TX_LEVEL]=512;
    while(audio_tx_info(AUDIO_FRAMES)<AE350_AUDIO_SECONDS*48000u) {
        int rc=audio_tx_dma_done();
        if(!rc) {out[TX_BLOCKS]++;if(audio_tx_dma_start(0,TXBUF,BLOCK))finish(24);}
        else if(rc!=AE350_DMA_ERR_BUSY) {last_dma_result=rc;finish(25);}
        rc=mic_rx_dma_done();
        if(!rc) {
            out[RX_BLOCKS]++;
            window_peak=observe_capture(window_peak);
            if(mic_rx_dma_start(1,RXBUF,BLOCK))finish(26);
        } else if(rc!=AE350_DMA_ERR_BUSY) {last_dma_result=rc;finish(27);}
        unsigned level=audio_tx_info(AUDIO_LEVEL);
        if(level<out[MIN_TX_LEVEL])out[MIN_TX_LEVEL]=level;
        level=mic_rx_info(AUDIO_LEVEL);if(level>out[MAX_RX_LEVEL])out[MAX_RX_LEVEL]=level;
        uint32_t frames=audio_tx_info(AUDIO_FRAMES);
        if(!(out[CPU_WRITES]&63u) && frames>128u && (frames<95900u || frames>192100u)) {
            uint32_t word=audio_tx_info(AUDIO_LAST);out[65]++;out[68]=word;
            if(word) {
                if(out[66]<8u)out[72+out[66]]=word;
                out[66]++;
            }
            int32_t left=(int16_t)word,right=(int16_t)(word>>16);
            if(left>1024||left< -1024||right>1024||right< -1024)out[67]++;
        }
        if(frames>=next_tick){
            puts("AE350 AUDIO TICK peak=");hex(window_peak);
#ifdef AE350_SD_STRESS
            puts(" sd=");hex(out[129]);
#endif
            puts("\r\n");window_peak=0;next_tick+=480000u;
        }
        if(frames!=last_frames){last_frames=frames;last_progress=cycles();}
        else if(expired(last_progress))finish(28);
        if(!(out[CPU_WRITES]&255u) && (audio_tx_info(AUDIO_UNDERRUN)||mic_rx_info(MIC_OVERRUN)))finish(30);
        audio_tx_mute(frames>=96000u&&frames<192000u);
        unsigned index=out[CPU_WRITES]&16383u; /* 64KiB exceeds the 32KiB D cache */
        CPU_BUF[index]=out[CPU_WRITES]^GUARD;
        if(CPU_BUF[index]!=(out[CPU_WRITES]^GUARD))finish(29);
        out[CPU_WRITES]++;
#ifdef AE350_SD_STRESS
        if(frames-last_sd_frame>=480u) {
            last_sd_frame=frames;
            unsigned error=(unsigned)sd_step();if(error)finish(0x310u+error);
        }
#endif
    }
    if(audio_tx_pause())finish(48); /* freeze paired counters before separate APB reads */
    out[FRAMES]=audio_tx_info(AUDIO_FRAMES);out[PLAYED]=audio_tx_info(AUDIO_PLAYED);
    out[UNDERRUNS]=audio_tx_info(AUDIO_UNDERRUN);out[RX_OVERRUNS]=mic_rx_info(MIC_OVERRUN);
    out[ACCEPTED]=mic_rx_info(MIC_ACCEPTED);
    if(audio_tx_stop()||mic_rx_stop())finish(31);
    if(ae350_dma_sync_for_cpu((uintptr_t)TXBUF-32,32)||
       ae350_dma_sync_for_cpu((uintptr_t)TXBUF+BLOCK*4u,32)||
       ae350_dma_sync_for_cpu((uintptr_t)RXBUF-32,32)||
       ae350_dma_sync_for_cpu((uintptr_t)RXBUF+BLOCK*4u,32))finish(33);
    out[GUARDS]=(W((uintptr_t)TXBUF-4)!=GUARD)+(W((uintptr_t)TXBUF+BLOCK*4u)!=GUARD)+
        (W((uintptr_t)RXBUF-4)!=GUARD)+(W((uintptr_t)RXBUF+BLOCK*4u)!=GUARD);
    if(out[UNDERRUNS]||out[RX_OVERRUNS]||out[RX_BAD_SIGN]||out[GUARDS])finish(30);
    finish(0);
    return 0;
}
