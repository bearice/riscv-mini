/* dg_debug.c - UART debug console for the BIOS DOOM payload.
 *
 * See dg_debug.h for the purpose.  Everything goes through the BIOS ABI:
 * BIOS_GETC for input, BIOS_WRITE for output, BIOS_VIDEO_PRESENT for the test
 * patterns.  Frame dumps travel as base64 between a DBGDUMP header and a DBGEND
 * trailer holding the CRC32 of the raw bytes, because the UART is the only
 * channel and the shim's stdout is unbuffered.
 */
#include "dg_debug.h"
#include "shim_private.h"
#include "../../bios/include/bios.h"	/* same relative path as dg_bios.c */
#include "doomstat.h"		/* gamestate, gameepisode, gamemap, viewplayer */
#include "d_loop.h"		/* gametic, ticdup */
#include "r_state.h"		/* viewx, viewy, viewz, viewangle */
#include <stdio.h>
#include <stdlib.h>

static unsigned g_ms[DBG_SLOTS],g_count[DBG_SLOTS];
static unsigned g_window_start,g_presents,g_present_fails;
static uintptr_t g_fb[2];
static unsigned g_stride,g_bpp,g_width,g_height,g_displayed;
static unsigned g_lb_x,g_lb_y,g_lb_y_raw,g_lb_end,g_game_w,g_game_h,g_game_bytes;
/* Set only around a dump's base64 body.  The shim's console writer checks it so
 * log lines cannot land in the middle of a dump; see dbg_uart_busy(). */
static int g_uart_busy;
int dbg_uart_busy(void) { return g_uart_busy; }
static const void *g_game;
static unsigned g_paused,g_paused_at,g_paused_ms,g_dumping,g_dump_at;
static unsigned g_clock_frozen,g_step,g_pattern;
/* Blank mode, see dbg_clear_view in dg_debug.h: 0 off, 1 black (the shipped
 * default), 2 index 0xff for the unwritten-pixel evidence dump.  A separate
 * declaration because the initialiser is the whole point. */
static unsigned g_clear=1;
/* A dump is only worth taking when the frame buffer holds a whole frame: it is
 * blanked at the start of one and complete at the end of one.  The shim's pump
 * can hand a command over in the middle, so a dump arriving there waits for the
 * next frame boundary instead of reporting a half-drawn (or freshly blanked)
 * buffer.  The queue preserves the order a human typed them in, because a host
 * pairing an 8-bit dump with the palette dump that follows it depends on it. */
static unsigned g_frame_ready=1;
static unsigned char g_queue[8];
static unsigned g_queued;

unsigned dbg_now_ms(void) {
    return (unsigned)bios_call(BIOS_TIME,0,0,0,0);
}
void dbg_add(unsigned slot,unsigned delta_ms) {
    if(slot>=DBG_SLOTS)return;
    g_ms[slot]+=delta_ms;++g_count[slot];
}
/* The game clock with every held second removed.  i_timer.c derives I_GetTime
 * from DG_GetTicksMs alone, so holding this holds the whole game; releasing it
 * without an offset would make DOOM try to catch up on a 15 s frame dump. */
uint32_t dbg_clock(uint32_t real_ms) {
    if(g_paused||g_dumping)return g_clock_frozen;
    return real_ms-g_paused_ms;
}
int dbg_frozen(void) {
    return g_paused&&!g_step;
}
/* Called by I_StartFrame: from here until dbg_frame_done the buffer is
 * mid-frame, and a dump would capture the engine's work in progress. */
void dbg_frame_began(void) {
    g_frame_ready=0;
}
void dbg_frame_done(void) {
    /* The frame buffer now holds a complete drawn frame, which is the only
     * state a dump is worth taking in. */
    g_frame_ready=1;
    if(g_step)--g_step;
}
void dbg_presented(void) {
    ++g_presents;
}
void dbg_present_failed(void) {
    ++g_present_fails;
}
/* Which slot the panel is reading right now.  Without this the dumps would
 * follow the slot DG_Init happened to start on: while the game runs that slot
 * is being overwritten every other frame, so a dump would mix two frames. */
void dbg_displayed(unsigned slot) {
    g_displayed=slot;
}
static const void *g_palette;
static unsigned g_palette_bytes;
void dbg_palette(const void *colors,unsigned bytes) {
    g_palette=colors;g_palette_bytes=bytes;
}
int dbg_hold(void) {
    return g_pattern!=0;
}
/* Blank-the-buffer mode, ON by default and toggled by `c`.  The clear itself
 * lives in I_StartFrame, which is the last point before the engine starts
 * drawing. */
int dbg_clear_view(void) {
    return (int)g_clear;
}
void dbg_video(uintptr_t fb0,uintptr_t fb1,unsigned stride,unsigned bytes_per_pixel,
	       unsigned width,unsigned height,unsigned displayed) {
    int first=!g_stride;
    g_fb[0]=fb0;g_fb[1]=fb1;g_stride=stride;g_bpp=bytes_per_pixel;
    g_width=width;g_height=height;g_displayed=displayed;
    if(first)printf("debug: UART console live - '?' lists commands\r\n");
}
void dbg_blit(unsigned started_ms,int x_offset,int y_offset,int x_offset_end,
	      const void *game_buffer,unsigned game_w,unsigned game_h) {
    dbg_add(DBG_BLIT,dbg_now_ms()-started_ms);
    g_lb_x=(unsigned)x_offset;g_lb_end=(unsigned)x_offset_end;
    /* i_video.c reports the byte offset of the picture's top edge inside the
     * slot; dump_area() crops by row, so convert.  The dump has to follow the
     * code, not the other way round. */
    g_lb_y=g_stride?(unsigned)y_offset/g_stride:0;g_lb_y_raw=(unsigned)y_offset;
    g_game=game_buffer;g_game_w=game_w;g_game_h=game_h;g_game_bytes=game_w*game_h;
}

/* ---- output ---------------------------------------------------------- */
static void wr(const void *data,unsigned n) {
    const char *s=data;
    while(n) {unsigned k=n>1024?1024:n;bios_call(BIOS_WRITE,(uintptr_t)s,k,0,0);s+=k;n-=k;}
}

/* ---- base64 ---------------------------------------------------------- */
/* One continuous stream over the whole dump: padding is emitted only by
 * b64_end(), because '=' is also valid base64 data and a decoder stops at the
 * first one it sees.  Encoding row by row truncated every dump at row one. */
static const char B64[]="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
static char line[96];static unsigned llen;
static unsigned char grp[3];static unsigned gcount;
static void b64_flush(void) {
    if(!llen)return;
    line[llen++]='\r';line[llen++]='\n';
    wr(line,llen);llen=0;
}
static void b64_put(char c) {
    line[llen++]=c;if(llen>=76)b64_flush();
}
static void b64_triple(unsigned v,int n) {
    b64_put(B64[(v>>18)&63]);b64_put(B64[(v>>12)&63]);
    b64_put(n>1?B64[(v>>6)&63]:'=');b64_put(n>2?B64[v&63]:'=');
}
static void b64_byte(unsigned char c) {
    grp[gcount++]=c;
    if(gcount==3) {
	b64_triple((unsigned)((grp[0]<<16)|(grp[1]<<8)|grp[2]),3);
	gcount=0;
    }
}
static void b64_end(void) {
    if(gcount) {
	unsigned v=(unsigned)(grp[0]<<16);
	if(gcount>1)v|=(unsigned)(grp[1]<<8);
	b64_triple(v,(int)gcount);
	gcount=0;
    }
    b64_flush();
}

/* ---- frame dump ------------------------------------------------------ */
/* One pass: a row is copied out of the DDR slot (the LCD DMA reads the same
 * slot, so a row copy is as consistent as it gets), then CRCed and encoded from
 * the copy.  A frame that changes mid-dump is caught by the CRC in DBGEND,
 * which is why scripts/doom_debug.py pauses the game before asking for one.
 * The dump also holds the game clock, so a 15 s transfer is not a 15 s warp. */
static unsigned char scratch[1024];
static void report_state(void);
static void dump_begin(void) {
    g_clock_frozen=dbg_now_ms()-g_paused_ms;g_dump_at=dbg_now_ms();++g_dumping;
}
static void dump_end(void) {
    g_paused_ms+=dbg_now_ms()-g_dump_at;--g_dumping;
}
static void dump_area(const char *kind,const volatile unsigned char *base,
		      unsigned stride,unsigned x0,unsigned y0,unsigned w,unsigned h,
		      unsigned bpp) {
    unsigned row=w*bpp,crc=~0u;
    if(!base||!row||row>sizeof scratch||!h) {
	printf("DBGERR %s unsupported row=%u bytes\r\n",kind,row);return;
    }
    dump_begin();
    /* The game state travels with every dump, printed BEFORE the header so the
     * host parser still sees the base64 body as the very next line.  Demo
     * playback is deterministic, so this is what lets a board frame and a sim
     * frame be matched by game position rather than by wall clock, which never
     * line up at 3 fps against 40 fps.  leveltime is the anchor to use: gametic
     * keeps counting across the title screen, so when the demo starts depends on
     * when the last key was pressed. */
    report_state();
    printf("DBGDUMP %s %u %u %u %u %u\r\n",kind,row*h,w,h,bpp,stride);
    /* Only the base64 body needs exclusivity: the header and trailer are whole
     * lines the host parser skips any junk around.  Muting for the body is what
     * keeps a heartbeat printf from landing inside the stream. */
    g_uart_busy=1;
    for(unsigned y=0;y<h;++y) {
	const volatile unsigned char *src=base+(size_t)(y0+y)*stride+x0;
	for(unsigned i=0;i<row;++i)scratch[i]=(unsigned char)src[i];
	crc=shim_crc32(crc,scratch,row);
	for(unsigned i=0;i<row;++i)b64_byte(scratch[i]);
    }
    g_uart_busy=0;
    b64_end();
    printf("DBGEND %08x\r\n",~crc);	/* standard CRC32: complemented */
    dump_end();
}
static void dump_drawn(void) {
    /* The 320x200 image I_FinishUpdate wrote into the letterboxed slot. */
    if(!g_game_bytes) {printf("DBGERR no blit has run yet\r\n");return;}
    dump_area("rgb565",(const void *)g_fb[g_displayed],g_stride,
	      g_lb_x,g_lb_y,g_game_w,g_game_h,g_bpp);
}
static void dump_slot(void) {
    /* Both slots, not just the displayed one.  While the game is frozen nothing
     * draws, so the two must hold the same picture: if they differ, the panel is
     * alternating between a current and a stale image and the player sees that
     * as flicker.  Reading only g_displayed could never show it. */
    if(g_fb[0]) dump_area("slot0",(const void *)g_fb[0],g_stride,0,0,g_width,g_height,g_bpp);
    if(g_fb[1]) dump_area("slot1",(const void *)g_fb[1],g_stride,0,0,g_width,g_height,g_bpp);
}
static void dump_game(void) {
    /* 8-bit source pixels plus the exact palette table the blit applies.  The
     * host does the RGB565 conversion itself, so anything that still differs
     * from the rgb565 dump is the write path and nothing else. */
    if(!g_game||!g_game_bytes) {printf("DBGERR no game buffer yet\r\n");return;}
    dump_begin();
    /* Same rule as dump_area: the state line must precede the header, because
     * the host parser treats every line after the header as base64.  A `g`
     * dump is worthless without knowing which game tic it captured. */
    report_state();
    unsigned crc=~0u;
    const unsigned char *p=g_game;
    printf("DBGDUMP index8 %u %u %u 1 %u\r\n",g_game_bytes,g_game_w,g_game_h,g_game_w);
    g_uart_busy=1;
    for(unsigned off=0;off<g_game_bytes;off+=sizeof scratch) {
	unsigned n=g_game_bytes-off;if(n>sizeof scratch)n=sizeof scratch;
	for(unsigned i=0;i<n;++i)scratch[i]=p[off+i];
	crc=shim_crc32(crc,scratch,n);
	for(unsigned i=0;i<n;++i)b64_byte(scratch[i]);
    }
    g_uart_busy=0;
    b64_end();
    printf("DBGEND %08x\r\n",~crc);	/* standard CRC32: complemented */
    if(g_palette&&g_palette_bytes&&g_palette_bytes<=sizeof scratch) {
	const unsigned char *q=g_palette;
	crc=~0u;
	for(unsigned i=0;i<g_palette_bytes;++i)scratch[i]=q[i];
	crc=shim_crc32(crc,scratch,g_palette_bytes);
	printf("DBGDUMP palette %u %u 1 1 %u\r\n",
	       g_palette_bytes,g_palette_bytes,g_palette_bytes);
	g_uart_busy=1;
	for(unsigned i=0;i<g_palette_bytes;++i)b64_byte(scratch[i]);
	g_uart_busy=0;
	b64_end();
	printf("DBGEND %08x\r\n",~crc);
    } else {
	printf("DBG note: no palette registered (I_SetPalette has not run)\r\n");
    }
    dump_end();
}

/* ---- test patterns --------------------------------------------------- */
static unsigned short bar8(unsigned i) {
    static const unsigned short c[8]={0xffff,0xffe0,0x07ff,0x07e0,0xf81f,0xf800,0x001f,0};
    return c[i&7];
}
/* Shown on the free slot and then held: DG_DrawFrame stops presenting while a
 * pattern is up, so the panel keeps a known image while the game goes on
 * hammering DDR.  Bands in a known image are a display-path fault, not DOOM. */
static void pattern(unsigned id) {
    if(!g_fb[0]||g_bpp!=2)return;
    unsigned slot=g_displayed^1u;
    unsigned short *fb=(unsigned short *)g_fb[slot];
    for(unsigned y=0;y<g_height;++y) {
	unsigned short *row=fb+(size_t)y*(g_stride/2);
	for(unsigned x=0;x<g_width;++x) {
	    unsigned short v;
	    if(id==1)v=bar8(x/(g_width/8u));			  /* wide vertical bars */
	    else if(id==2)v=((x^y)&1)?0xffffu:0u;		  /* 1-px checker */
	    else if(id==3)v=(((x>>2)^(y>>2))&1)?0xffffu:0u;	  /* 4x4 checker */
	    else if(id==4)v=bar8(y/(g_height/8u));		  /* wide horizontal bars */
	    else v=(unsigned short)((((x*255u)/g_width)<<8)|(((y*255u)/g_height)<<3)|(x>>3));
	    row[x]=v;
	}
    }
    if(bios_call(BIOS_VIDEO_PRESENT,slot,0,0,0)!=0)++g_present_fails;
    g_displayed=slot;g_pattern=id;
}

/* ---- reports --------------------------------------------------------- */
static void report_timing(void) {
    static const char *name[DBG_SLOTS]={"tick","blit","draw","sleep","poll"};
    unsigned now=dbg_now_ms(),span=now-g_window_start;
    printf("DBG timing window=%u ms frames=%u present_fails=%u fps=%u.%02u\r\n",
	   span,g_presents,g_present_fails,
	   span?g_presents*1000u/span:0,span?(g_presents*100000u/span)%100u:0);
    for(unsigned i=0;i<DBG_SLOTS;++i)
	printf("  %-5s ms=%u n=%u avg=%u\r\n",name[i],g_ms[i],g_count[i],
	       g_count[i]?g_ms[i]/g_count[i]:0);
    for(unsigned i=0;i<DBG_SLOTS;++i)g_ms[i]=g_count[i]=0;
    g_window_start=now;g_presents=0;
}
static void report_video(void) {
    printf("DBG video fb0=0x%08x fb1=0x%08x stride=%u bpp=%u %ux%u displayed=%u\r\n",
	   (unsigned)g_fb[0],(unsigned)g_fb[1],g_stride,g_bpp,g_width,g_height,g_displayed);
    printf("  letterbox x=%u y=%u rows (%u bytes) end=%u game=%ux%u pattern=%u paused=%u\r\n",
	   g_lb_x,g_lb_y,g_lb_y_raw,g_lb_end,g_game_w,g_game_h,g_pattern,g_paused);
}
/* Demo playback is deterministic: the demo file, not real time, decides which
 * ticcmd runs at which gametic, so two machines that agree on the WAD must show
 * the same picture at the same gametic.  That makes this line the anchor for
 * comparing the board against the QEMU sim: equal gametic and a different
 * picture is a real porting bug, an equal picture is what the demo genuinely
 * looks like at that point (a camera pressed against geometry renders as wide
 * constant-colour bands, which is easy to mistake for a display fault). */
static void report_state(void) {
    printf("DBG state gametic=%d lt=%d ticdup=%d state=%d ep=%d map=%d demo=%d"
	   " viewx=%d viewy=%d viewz=%d angle=0x%08x health=%d\r\n",
	   gametic, leveltime, ticdup, (int)gamestate, gameepisode, gamemap,
	   (int)demoplayback, viewx, viewy, viewz, (unsigned)viewangle,
	   viewplayer ? viewplayer->health : -999);
}
static void report_io(void) {
    struct dbg_io io={0,0,0,0,0,0};
    shim_io_snapshot(&io);
    printf("DBG io bytes=%u KiB fs=%u ms/%u calls poll=%u ms/%u calls readcrc=0x%08x\r\n",
	   (unsigned)(io.bytes>>10),(unsigned)io.fs_ms,(unsigned)io.fs_calls,
	   (unsigned)io.poll_ms,(unsigned)io.poll_calls,(unsigned)io.crc);
}
static void help_text(void) {
    printf("DBG commands (single character, no echo)\r\n"
	   "  p pause/resume  s step one frame (while paused)\r\n"
	   "  f dump the drawn 320x200  F dump the whole slot  g dump 8-bit source\r\n"
	   "  t timing  i file io  v video  m game state  x exit to BIOS\r\n"
	   "  c cycle frame blanking: black (default) -> 0xff evidence -> off\r\n"
	   "  1..5 test pattern (game keeps running)  0 release the pattern\r\n");
}

/* ---- command loop ---------------------------------------------------- */
static void discard_keys(void) {
    struct bios_key k;
    while(bios_call(BIOS_KEY,(uintptr_t)&k,0,0,0)==0) ;
}
static void command(int c) {
    switch(c) {
    case '?':case 'h':help_text();break;
    case 'p':
	if(g_paused) {
	    g_paused_ms+=dbg_now_ms()-g_paused_at;g_paused=0;
	    printf("DBG run\r\n");
	} else {
	    g_paused_at=dbg_now_ms();g_clock_frozen=g_paused_at-g_paused_ms;g_paused=1;
	    printf("DBG paused frames=%u\r\n",g_presents);
	}
	break;
    case 's':
	if(g_paused) {g_step=1;printf("DBG step\r\n");}
	break;
    case 'f':dump_drawn();break;
    case 'F':dump_slot();break;
    case 'g':dump_game();break;
    case 't':report_timing();break;
    case 'i':report_io();break;
    case 'v':report_video();break;
    case 'm':report_state();break;
    case 'c':
	g_clear=(g_clear+1u)%3u;
	printf("DBG blank=%s\r\n",g_clear==2?"0xff (evidence: a `g` dump now names every pixel the engine leaves unwritten)":
	       g_clear?"black (stale pixels cannot survive a transition)":"off (vanilla: unwritten pixels keep the previous frame)");
	break;
    case '1':case '2':case '3':case '4':case '5':
	pattern((unsigned)(c-'0'));printf("DBG pattern %u shown\r\n",(unsigned)(c-'0'));break;
    case '0':
	if(g_pattern) {g_pattern=0;printf("DBG pattern released\r\n");}
	break;
    case 'x':printf("DBG exit\r\n");exit(0);
    default:break;
    }
    /* One terminator per command.  'g' answers with two dumps, and a host that
     * stopped at the first DBGEND would silently drop the palette. */
    printf("DBG done\r\n");
}
static void service(int boundary) {
    int c;
    bios_call(BIOS_POLL,0,0,0,0);
    /* At a boundary the buffer holds a whole frame, so run anything that has
     * been waiting before reading more input. */
    if(boundary&&g_frame_ready) {
	while(g_queued) {
	    c=g_queue[0];
	    for(unsigned i=1;i<g_queued;++i)g_queue[i-1]=g_queue[i];
	    --g_queued;
	    command(c);
	}
    }
    /* Bounded: a channel that always reports a character must not starve the
     * frame loop, and one frame should not be spent entirely on commands. */
    for(int pending=0;pending<32;++pending) {
	c=bios_call(BIOS_GETC,0,0,0,0);
	if(c<0)break;
	if(!boundary&&g_frame_ready==0&&(c=='f'||c=='F'||c=='g')) {
	    if(g_queued<sizeof g_queue)g_queue[g_queued++]=(unsigned char)c;
	    continue;
	}
	command(c);
    }
    /* While the game is held nothing else drains the BIOS key ring, and a held
     * key repeats there, so resuming would deliver a burst of stale events. */
    if(g_paused)discard_keys();
    if(g_paused||g_dumping)bios_call(BIOS_POLL,0,0,0,0);
}
void dbg_service(void) {
    service(1);
}

/* The shim's throttled pump calls this so the console answers in the middle of
 * a long tick.  Re-entering here from a command is harmless: no command path
 * reaches fread(), and exit() leaves through the shim's setjmp guard. */
void shim_debug_hook(void) {
    service(0);
}
