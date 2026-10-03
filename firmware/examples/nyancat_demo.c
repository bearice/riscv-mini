/* BIOS payload: original GIF artwork, procedural stars/rainbow, looping BGM. */
#include "../bios/include/bios.h"
#include "nyancat_assets.h"

enum {WIDTH=480,HEIGHT=272,SCALE=8,CAT_X=104,CAT_Y=52,
      RING_FRAMES=32768,CHUNK=256,STARS=14};
static uint32_t ring[RING_FRAMES],pcm[CHUNK];
static unsigned sample,produced,loops,draws,muted,paused,failed,frame,slot;
static unsigned previous_tick[2],previous_frame[2],previous_valid[2];
static const uint16_t rainbow[]={0xf800,0xfcc0,0xffe0,0x37e0,0x04df,0x619f};
static const uint16_t sky=0x018c; /* #003366 in RGB565 */

static void say(const char *s) {
    unsigned n=0;while(s[n])++n;bios_call(BIOS_WRITE,(uintptr_t)s,n,0,0);
}
static void hex(unsigned value) {
    char chars[8];static const char digits[]="0123456789abcdef";
    for(unsigned i=0;i<8;++i)chars[i]=digits[value>>(28-4*i)&15];
    bios_call(BIOS_WRITE,(uintptr_t)chars,8,0,0);
}
static unsigned now(void) {return bios_call(BIOS_TIME,0,0,0,0);}
static void poll(void) {bios_call(BIOS_POLL,0,0,0,0);}
static int control(unsigned command) {return bios_call(BIOS_AUDIO_CONTROL,command,0,0,0);}
static void audio_yield(void) {
    /* Leave DDR idle briefly when graphics has drained the hardware FIFO.
       A full software ring does not guarantee that DMA has bus access. */
    for(unsigned attempt=0;attempt<64;++attempt) {
        struct bios_audio audio;audio.level=0;
        if(bios_call(BIOS_AUDIO_INFO,(uintptr_t)&audio,0,0,0)) {failed=1;return;}
        if(audio.level>=384)return;
        for(unsigned i=0;i<128;++i)__asm__ volatile("nop");
    }
}

/* Geometry is aligned to even pixels, allowing one DDR store per pixel pair. */
static void rect(volatile uint16_t *fb,int x,int y,int w,int h,uint16_t color) {
    if(x<0) {w+=x;x=0;}if(y<0) {h+=y;y=0;}
    if(x+w>WIDTH)w=WIDTH-x;
    if(y+h>HEIGHT)h=HEIGHT-y;
    if(w<=0 || h<=0)return;
    uint32_t pair=(uint32_t)color<<16|color;
    for(int row=0;row<h;++row) {
        volatile uint32_t *dest=(volatile uint32_t *)(fb+(y+row)*WIDTH+x);
        for(int col=0;col<w/2;++col)dest[col]=pair;
        /* Release long CPU DDR write runs so audio DMA can refill its FIFO. */
        if(w>=64 && (row&7)==7)poll();
    }
}

static void star(volatile uint16_t *fb,unsigned tick,unsigned index,unsigned erase) {
    /* All positions/arms are even. Skip the cat/rainbow region, so restoration
       never damages the artwork or needs a full framebuffer redraw. */
    int x=(int)((index*136u+476u-(tick*8u)%480u)%480u);
    int y=(int)((index*68u+20u)%264u);
    if(y+10>44 && y-8<224 && x-8<384)return;
    uint16_t color=erase?sky:0xffff;
    if(erase) {rect(fb,x-8,y-8,18,18,color);return;}
    unsigned phase=(tick+index)%6;
    rect(fb,x,y,2,2,color);
    if(phase==1 || phase==4) {rect(fb,x-4,y,10,2,color);rect(fb,x,y-4,2,10,color);}
    if(phase==2 || phase==3) {
        rect(fb,x-8,y,4,2,color);rect(fb,x+6,y,4,2,color);
        rect(fb,x,y-8,2,4,color);rect(fb,x,y+6,2,4,color);
    }
}

static void render(volatile uint16_t *fb,unsigned tick) {
    if(previous_valid[slot])for(unsigned i=0;i<STARS;++i)star(fb,previous_tick[slot],i,1);
    for(unsigned i=0;i<STARS;++i)star(fb,tick,i,0);
    /* Alternating segments provide the waving rainbow trail behind the cat. */
    rect(fb,0,68,160,8,sky);rect(fb,0,196,160,8,sky);
    for(unsigned x=0;x<160;x+=16) {
        unsigned wave=((x/16+tick)&1)*4;
        for(unsigned band=0;band<6;++band)rect(fb,x,76+wave+band*20,16,20,rainbow[band]);
    }
    const uint8_t *cells=nyan_frames[frame];
    const uint8_t *old=previous_valid[slot]?nyan_frames[previous_frame[slot]]:0;
    for(unsigned y=0;y<NYAN_ROWS;++y) {
        for(unsigned x=0;x<NYAN_COLS;) {
            unsigned at=y*NYAN_COLS+x,color=cells[at],end=x+1;
            if(x>=7 && color==(old?old[at]:0)) {++x;continue;}
            while(end<NYAN_COLS && cells[y*NYAN_COLS+end]==color &&
                  (end<7 || color!=(old?old[y*NYAN_COLS+end]:0)) &&
                  (color || x>=7))++end;
            if(color)rect(fb,CAT_X+x*SCALE,CAT_Y+y*SCALE,(end-x)*SCALE,SCALE,nyan_palette[color]);
            else if(x>=7)rect(fb,CAT_X+x*SCALE,CAT_Y+y*SCALE,(end-x)*SCALE,SCALE,sky);
            else {
                /* Transparent tail cells reveal either navy or moving rainbow. */
                unsigned gx=CAT_X+x*SCALE,wave=((gx/16+tick)&1)*4;
                for(unsigned row=0;row<SCALE;++row) {
                    int ry=(int)(CAT_Y+y*SCALE+row)-(int)(76+wave);
                    uint16_t bg=ry>=0 && ry<120?rainbow[ry/20]:sky;
                    rect(fb,gx,CAT_Y+y*SCALE+row,SCALE,1,bg);
                }
            }
            x=end;
        }
        if(!(y&3)) {poll();audio_yield();}
    }
    previous_tick[slot]=tick;previous_frame[slot]=frame;previous_valid[slot]=1;
}

static void refill(void) {
    unsigned samples=(unsigned)(nyan_pcm_end-nyan_pcm);
    struct bios_audio audio;
    if(bios_call(BIOS_AUDIO_INFO,(uintptr_t)&audio,0,0,0)) {failed=1;return;}
    unsigned used=produced-audio.fetched;
    if(used>RING_FRAMES) {failed=1;return;}
    /* Generate a chunk only when there is room for it. Generating 256 samples
       just to accept a handful wastes DDR reads and CPU time during idle. */
    unsigned batches=(RING_FRAMES-used)/CHUNK;
    for(unsigned batch=0;batch<batches;++batch) {
        unsigned index=sample;
        for(unsigned i=0;i<CHUNK;++i) {
            unsigned value=(uint16_t)nyan_pcm[index];pcm[i]=value|(value<<16);
            if(++index==samples)index=0;
        }
        int written=bios_call(BIOS_AUDIO_WRITE,(uintptr_t)pcm,CHUNK,0,0);
        if(written<0) {failed=1;return;}
        if(!written)return;
        produced+=(unsigned)written;
        sample+=(unsigned)written;
        if(sample>=samples) {sample-=samples;++loops;}
        if(written<CHUNK)return;
        if(!(batch&7))poll();
    }
}

static void status(unsigned max_render_ms) {
    struct bios_audio audio;
    if(bios_call(BIOS_AUDIO_INFO,(uintptr_t)&audio,0,0,0)) {failed=1;return;}
    say("NYAN frames=");hex(draws);say(" frame=");hex(frame);say(" music_loops=");hex(loops);
    say(" max_render_ms=");hex(max_render_ms);say(" played=");hex(audio.played);
    say(" produced=");hex(produced);say(" fetched=");hex(audio.fetched);say(" level=");hex(audio.level);
    say(" underruns=");hex(audio.underruns);say(" errors=");hex(audio.errors);say("\r\n");
    /* An underrun inserts silence, then DMA resumes; keep the demo running
       and report it. Configuration/bus errors and overflow remain fatal. */
    if(audio.overruns || audio.errors)failed=1;
}

int payload_main(const struct bios_info *info) {
    if(info->width!=WIDTH || info->height!=HEIGHT || info->stride!=WIDTH*2 ||
       info->format!=565 || !(info->features&(1u<<4))) {
        say("NYAN requires 480x272 RGB565 and BIOS audio services\r\n");return 1;
    }
    struct bios_audio audio;
    if(bios_call(BIOS_AUDIO_INFO,(uintptr_t)&audio,0,0,0) || audio.sample_rate!=48000) {
        say("NYAN requires 48000 Hz audio\r\n");return 1;
    }
    if(bios_call(BIOS_VIDEO_MODE,BIOS_GRAPHICS,0,0,0) ||
       bios_call(BIOS_AUDIO_BEGIN,(uintptr_t)ring,RING_FRAMES,0,0))return 1;
    for(unsigned i=0;i<2;++i) {
        volatile uint16_t *fb=(void *)(uintptr_t)info->framebuffer[i];
        for(unsigned y=0;y<HEIGHT;y+=8) {rect(fb,0,y,WIDTH,8,sky);poll();}
    }
    refill();
    unsigned deadline=now()+100;
    do {poll();bios_call(BIOS_AUDIO_INFO,(uintptr_t)&audio,0,0,0);}while(audio.level<256 && (int32_t)(now()-deadline)<0);
    if(failed || audio.level<256 || control(BIOS_AUDIO_PLAY) || control(BIOS_AUDIO_UNMUTE)) {failed=1;goto done;}
    say("NYAN READY: q=return to BIOS, space=pause, m=mute, s=status\r\n");
    unsigned next=now(),tick=0,max_render=0,report=next+10000;
    for(;;) {
        poll();refill();if(failed)break;
        int c=bios_call(BIOS_GETC,0,0,0,0);
        if(c=='q' || c=='Q' || c==27)break;
        if(c=='!')bios_call(BIOS_REBOOT,0,0,0,0);
        if(c=='m' || c=='M') {muted^=1;control(muted?BIOS_AUDIO_MUTE:BIOS_AUDIO_UNMUTE);}
        if(c==' ') {paused^=1;control(paused?BIOS_AUDIO_PAUSE:BIOS_AUDIO_PLAY);next=now();}
        if(c=='s' || (int32_t)(now()-report)>=0) {status(max_render);report=now()+10000;}
        if(paused || (int32_t)(now()-next)<0)continue;
        unsigned start=now();
        render((void *)(uintptr_t)info->framebuffer[slot],tick++);
        if(bios_call(BIOS_VIDEO_PRESENT,slot,0,0,0)) {failed=1;break;}
        ++draws;slot^=1;
        unsigned elapsed=now()-start;if(elapsed>max_render)max_render=elapsed;
        next+=nyan_duration[frame];frame=(frame+1)%NYAN_FRAMES;
        /* Slow rendering never triggers a catch-up loop that starves audio. */
        if((int32_t)(now()-next)>0)next=now();
    }
    status(max_render);
done:
    control(BIOS_AUDIO_STOP);control(BIOS_AUDIO_MUTE);
    bios_call(BIOS_VIDEO_MODE,BIOS_TEXT,0,0,0);
    say(failed?"NYAN FAIL\r\n":"NYAN STOP\r\n");return failed?1:0;
}
