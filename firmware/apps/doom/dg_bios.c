/* DOOM platform layer for the riscv-mini resident BIOS (ABI v1).
 *
 * Video : 480x272 panel, double buffered. RGB565 converts the 320x200
 *         engine surface into the inactive BIOS slot. DOOM_INDEX8 copies its
 *         index rows instead and uploads the gamma-adjusted RGB565 palette to
 *         the inactive hardware bank. Present commits slot and bank together;
 *         the hardware viewport keeps the centered letterbox black.
 * Input : raw USB HID usage codes from BIOS_KEY (page 0x07) mapped to doomkeys.
 *         UART is log output only, per the port's scope.
 * Time  : BIOS millisecond counter; every DG_ call also pumps BIOS_POLL so the
 *         USB stack keeps running (the BIOS only services USB inside hal_poll).
 * Exit  : F12 calls exit(), which longjmps out of D_DoomMain back to
 *         payload_main; text mode is restored and control returns to the BIOS.
 */
#include "../../bios/include/bios.h"
#include "dg_debug.h"
#include "doomgeneric.h"
#include "doomkeys.h"
#include "shim_private.h"
#include "runtime.h"
#ifdef DOOM_TIMEDEMO
#include "doomstat.h"
#endif
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static struct bios_info g_info;
static volatile uint16_t *g_fb[2];
static unsigned g_slot;
static unsigned g_video;
#ifdef DOOM_INDEX8
static unsigned g_palette_bank,g_pending_bank;
void DG_SetPalette(const uint16_t *palette) {
    g_pending_bank=g_palette_bank^1;
    if(bios_call(BIOS_VIDEO_PALETTE,(uintptr_t)palette,g_pending_bank,256,0)!=0)
        exit(2);
    uint16_t readback[256];
    if(bios_call(BIOS_VIDEO_PALETTE_READ,(uintptr_t)readback,g_pending_bank,256,0)!=0
       || memcmp(palette,readback,sizeof readback))exit(3);
}
#endif

static uint32_t ms_now(void) {return (uint32_t)bios_call(BIOS_TIME,0,0,0,0);}
/* BIOS_POLL is not free - it runs the USB host stack and the Ethernet poll - and
 * it is reached from the clock, the sleep and the present path alike, so it is
 * timed once here.  Read it as nested inside whichever section called it.
 * Measured on hardware: 2 904 POLLs in 41 s at 0.68 ms each (15 ms of every
 * frame) because I_GetTicks reaches it dozens of times per tick.  Throttle it
 * here rather than at each call site; 20 ms is still a 50 Hz keyboard poll. */
static void pump(void) {
    static uint32_t last;
    uint32_t t=ms_now();
    if((uint32_t)(t-last)<20)return;
    last=t;
    unsigned started=dbg_now_ms();
    bios_call(BIOS_POLL,0,0,0,0);
    dbg_add(DBG_POLL,dbg_now_ms()-started);
}

void DG_Init(void) {
    #ifdef DOOM_INDEX8
    if(bios_call(BIOS_VIDEO_MODE,BIOS_GRAPHICS_INDEX8,0,0,0)!=0)exit(4);
    if(bios_call(BIOS_VIDEO_WINDOW,80,36,400,236)!=0)exit(5);
#else
    if(bios_call(BIOS_VIDEO_MODE,BIOS_GRAPHICS,0,0,0)!=0)return;
#endif
    if(bios_call(BIOS_INFO,(uintptr_t)&g_info,0,0,0)!=0)return;
    if(!g_info.framebuffer[0]||!g_info.framebuffer[1])return;
#ifdef DOOM_INDEX8
    if(g_info.format!=8 || g_info.stride!=480)exit(6);
#endif
    unsigned stride=g_info.stride?g_info.stride:(g_info.width*2);
    for(unsigned i=0;i<2;++i) {
        g_fb[i]=(volatile uint16_t *)(uintptr_t)g_info.framebuffer[i];
        /* Clear the whole slot: rows below the 200 line are never drawn. */
        for(unsigned k=0;k<stride*g_info.height/2;++k)g_fb[i][k]=0;
    }
    g_slot=0;
    g_video=1;
    DG_ScreenBuffer=(pixel_t *)g_fb[g_slot];
    dbg_video((uintptr_t)g_fb[0],(uintptr_t)g_fb[1],stride,g_info.format==8?1:2,
              g_info.width,g_info.height,g_slot);
    printf("DG_Init: %ux%u fb=%p stride=%u\n",g_info.width,g_info.height,
           (void *)g_fb[0],stride);
}

void DG_DrawFrame(void) {
    static unsigned frames;
    if(!g_video)return;
    unsigned started=dbg_now_ms();
    pump();
    /* A test pattern is held on the panel by the debug console; presenting now
     * would overwrite the image being inspected. */
    if(!dbg_hold()) {
        #ifdef DOOM_INDEX8
        int presented=bios_call(BIOS_VIDEO_PRESENT_INDEX8,g_slot,g_pending_bank,0,0);
        if(!presented)g_palette_bank=g_pending_bank;
#else
        int presented=bios_call(BIOS_VIDEO_PRESENT,g_slot,0,0,0);
#endif
        if(presented!=0)dbg_present_failed();
        else {dbg_presented();dbg_displayed(g_slot);}
        g_slot^=1;                                /* render into the other slot */
        DG_ScreenBuffer=(pixel_t *)g_fb[g_slot];
        /* UART heartbeat: the board acceptance reads this to tell "rendering"
         * from "stuck"; the game never reads the UART.  It counts presented
         * frames only, so a held test pattern goes quiet on purpose. */
        if(++frames%100==0)printf("DOOM presents=%u ms=%u\n",frames,(unsigned)ms_now());
    }
    dbg_add(DBG_DRAW,dbg_now_ms()-started);
}

void DG_SleepMs(uint32_t ms) {
    uint32_t start=ms_now();
    /* While the debug console holds the game clock, a real-time wait is the only
     * code that runs: the screen wipe spins here waiting for I_GetTime(), and the
     * pause froze exactly that clock.  Servicing here is what keeps the console
     * answering - and a queued frame dump running - instead of going dark until
     * someone resumes. */
    while((uint32_t)(ms_now()-start)<ms) {
        pump();
        if(dbg_held())dbg_service_held();
    }
    dbg_add(DBG_SLEEP,(unsigned)(ms_now()-start));
}

uint32_t DG_GetTicksMs(void) {
    pump();
    /* dbg_clock holds the game while the debug console is paused or dumping. */
    return dbg_clock(ms_now());
}

void DG_SetWindowTitle(const char *title) {(void)title;}

/* HID usage page 0x07 -> doomkeys.h. Both shift keys map to KEY_RSHIFT because
 * i_input.c only tracks shiftdown from KEY_RSHIFT. */
static unsigned char map_key(unsigned usage) {
    if(usage>=0x04&&usage<=0x1d)return (unsigned char)('a'+usage-0x04);
    if(usage>=0x1e&&usage<=0x26)return (unsigned char)('1'+usage-0x1e);
    if(usage>=0x3a&&usage<=0x45)return (unsigned char)(KEY_F1+(usage-0x3a));
    switch(usage) {
    case 0x27:return '0';
    case 0x28:return KEY_ENTER;
    case 0x29:return KEY_ESCAPE;
    case 0x2a:return KEY_BACKSPACE;
    case 0x2b:return KEY_TAB;
    case 0x2c:return ' ';
    case 0x2d:return '-';
    case 0x2e:return '=';
    case 0x2f:return '[';
    case 0x30:return ']';
    case 0x31:return '\\';
    case 0x33:return ';';
    case 0x34:return '\'';
    case 0x35:return '`';
    case 0x36:return ',';
    case 0x37:return '.';
    case 0x38:return '/';
    case 0x39:return KEY_CAPSLOCK;
    case 0x46:return KEY_PRTSCR;
    case 0x47:return KEY_SCRLCK;
    case 0x48:return KEY_PAUSE;
    case 0x49:return KEY_INS;
    case 0x4a:return KEY_HOME;
    case 0x4b:return KEY_PGUP;
    case 0x4c:return KEY_DEL;
    case 0x4d:return KEY_END;
    case 0x4e:return KEY_PGDN;
    case 0x4f:return KEY_RIGHTARROW;
    case 0x50:return KEY_LEFTARROW;
    case 0x51:return KEY_DOWNARROW;
    case 0x52:return KEY_UPARROW;
    case 0x53:return KEY_NUMLOCK;
    case 0x54:return KEYP_DIVIDE;
    case 0x55:return KEYP_MULTIPLY;
    case 0x56:return KEYP_MINUS;
    case 0x57:return KEYP_PLUS;
    case 0x58:return KEYP_ENTER;
    case 0x59:return (unsigned char)KEYP_1;
    case 0x5a:return (unsigned char)KEYP_2;
    case 0x5b:return (unsigned char)KEYP_3;
    case 0x5c:return (unsigned char)KEYP_4;
    case 0x5d:return (unsigned char)KEYP_5;
    case 0x5e:return (unsigned char)KEYP_6;
    case 0x5f:return (unsigned char)KEYP_7;
    case 0x60:return (unsigned char)KEYP_8;
    case 0x61:return (unsigned char)KEYP_9;
    case 0x62:return (unsigned char)KEYP_0;
    case 0x63:return (unsigned char)KEYP_PERIOD;
    case 0xe0:case 0xe4:return KEY_RCTRL;
    case 0xe1:case 0xe5:return KEY_RSHIFT;
    case 0xe2:case 0xe6:return KEY_RALT;
    default:return 0;
    }
}

int DG_GetKey(int *pressed,unsigned char *key) {
#ifdef DOOM_TIMEDEMO
    (void)pressed; (void)key;
    return 0; /* A benchmark's recorded inputs must not depend on the keyboard. */
#else
    for(unsigned spins=0;spins<64;++spins) {
        struct bios_key ev;
        if(bios_call(BIOS_KEY,(uintptr_t)&ev,0,0,0)!=0)break; /* queue empty */
        if(ev.pressed&&ev.usage==0x45)exit(0);                /* F12: leave DOOM */
        unsigned char doomkey=map_key(ev.usage);
        if(doomkey) {
            *pressed=ev.pressed?1:0;
            *key=doomkey;
            return 1;
        }
    }
    pump();
    return 0;
#endif
}

/* ------------------------------------------------------------------ payload */
static char arg_doom[]="doom";
static char arg_iwad[]="-iwad";
static char arg_wad[]="DOOM1.WAD";
static char arg_mb[]="-mb";
static char arg_mbval[]="16";
static char arg_nosound[]="-nosound";
static char arg_nomusic[]="-nomusic";
static char arg_nosfx[]="-nosfx";

static int doom_main(const struct bios_info *info) {
    (void)info;
    /* argv[argc] must be NULL, as for a normal C program. */
    char *argv[]={arg_doom,arg_iwad,arg_wad,arg_mb,arg_mbval,
#ifdef DOOM_TIMEDEMO
                  "-timedemo",DOOM_TIMEDEMO,
#endif
                  arg_nosound,arg_nomusic,arg_nosfx,0};
    doomgeneric_Create((int)(sizeof argv/sizeof*argv)-1,argv);
    /* doomgeneric contract: D_DoomMain returns after D_DoomLoop has run one
     * tick, so the platform owns the loop (see the other doomgeneric ports).
     * dbg_service() is the UART debug channel: it costs one BIOS_GETC per frame
     * and holds the loop while the console is paused (docs/platforms/primer20k_vexriscv/doom.md). */
    for(;;) {
        dbg_service();
        if(dbg_frozen())continue;
        unsigned started=dbg_now_ms();
#if defined(DOOM_TIMEDEMO) && DOOM_BENCH_FRAMES > 0
        static unsigned bench_start,bench_count,bench_lt;
        int measuring=gamestate==GS_LEVEL && leveltime>=16;
        if(measuring && !bench_count) {bench_start=started;bench_lt=leveltime;}
#endif
        doomgeneric_Tick();
        dbg_add(DBG_TICK,dbg_now_ms()-started);
        dbg_frame_done();
#if defined(DOOM_TIMEDEMO) && DOOM_BENCH_FRAMES > 0
        if(measuring && ++bench_count==DOOM_BENCH_FRAMES) {
            unsigned elapsed=dbg_now_ms()-bench_start;
            unsigned rate=elapsed ? (bench_count*100000u)/elapsed : 0;
            printf("DBG bench frames=%u elapsed=%u ms fps=%u.%02u start_lt=%u end_lt=%u\n",
                   bench_count,elapsed,rate/100,rate%100,bench_lt,(unsigned)leveltime);
#ifdef DOOM_BENCH_CHECKSUM
            dbg_benchmark_checksum(); /* outside the measured window */
#endif
            exit(0);
        }
#endif
    }
    return 0; /* unreachable: only exit() leaves the loop */
}

int payload_main(const struct bios_info *info) {
    int status=bios_libc_run(info,doom_main);
    bios_call(BIOS_VIDEO_MODE,BIOS_TEXT,0,0,0);
    static const char bye[]="DOOM: exited, back to BIOS console\r\n";
    bios_call(BIOS_WRITE,(uintptr_t)bye,sizeof bye-1,0,0);
    return status;
}
