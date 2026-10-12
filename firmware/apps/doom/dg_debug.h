/* dg_debug.h - UART debug channel for the BIOS DOOM payload.
 *
 * The board has no debugger, no screen capture and no JTAG, and the panel is
 * the only rich output the port has.  This module turns the UART - which the
 * resident BIOS already routes to a payload through BIOS_GETC / BIOS_WRITE -
 * into a pause / step / dump console, so a rendered frame can be pulled off the
 * board and compared with the QEMU reference instead of being judged by eye.
 *
 * It is diagnostic only: no command here changes what the game renders, and
 * with no UART input pending the cost is one BIOS_GETC ecall per frame.
 * Commands and the host side are documented in docs/doom.md ("调试通道") and
 * scripts/doom_debug.py.
 */
#ifndef DG_DEBUG_H
#define DG_DEBUG_H
#include <stdint.h>

/* Accumulated wall-clock milliseconds per section; 't' prints and resets them.
 * DBG_BLIT is filled by the hook in i_video.c, the rest by dg_bios.c.  The tick
 * section is the whole doomgeneric_Tick(), so the engine proper is
 * tick - blit - draw and the two sub-sections must be read as parts of it. */
#define DBG_TICK  0u	/* doomgeneric_Tick(), blit and present included */
#define DBG_BLIT  1u	/* I_FinishUpdate: palette conversion + copy to the slot */
#define DBG_DRAW  2u	/* DG_DrawFrame: BIOS_POLL + BIOS_VIDEO_PRESENT */
#define DBG_SLEEP 3u	/* DG_SleepMs */
#define DBG_POLL  4u	/* BIOS_POLL itself: USB host stack + Ethernet poll */
#define DBG_SLOTS 5u

/* Milliseconds since boot.  Never frozen, so timings stay honest. */
unsigned dbg_now_ms(void);

/* Add elapsed milliseconds to a section and count the sample. */
void dbg_add(unsigned slot,unsigned delta_ms);

/* Game clock: real_ms with every paused and every dumping second removed, so
 * neither a pause nor a 15 s frame dump makes i_timer.c try to catch up. */
uint32_t dbg_clock(uint32_t real_ms);

/* Non-zero while the tick loop must leave the game alone. */
int dbg_frozen(void);

/* Non-zero while the game clock is held (paused or mid-dump).  A real-time
 * wait is then the only code that runs, so DG_SleepMs services the console
 * when nothing else will: the screen wipe spins in I_Sleep() waiting for
 * I_GetTime(), and a pause freezes exactly that clock. */
int dbg_held(void);

/* One doomgeneric_Tick() finished; consumes a single-step budget. */
void dbg_frame_done(void);

/* One is about to start: called from I_StartFrame, where the buffer is also
 * blanked.  Between this and dbg_frame_done() the frame is half-drawn, so a
 * dump command arriving from the shim's mid-tick pump is held for the next
 * boundary rather than reporting a buffer mid-write. */
void dbg_frame_began(void);

/* Panel bookkeeping, reported by DG_DrawFrame: a present that timed out waiting
 * for the panel to flip is a fault in itself and must not pass silently. */
void dbg_presented(void);
void dbg_present_failed(void);
/* Slot the panel latched to, reported after a completed flip.  Dumps read the
 * displayed slot; the other one is what the game is drawing right now. */
void dbg_displayed(unsigned slot);
/* The exact struct color table the blit uses (256 x 4 bytes, BGRA in memory).
 * I_SetPalette pushes PLAYPAL through gammatable[usegamma] first, so comparing
 * a dump against the WAD's own PLAYPAL would blame the blit for a gamma step.
 * With this table the host reproduces the panel bit for bit or the fault is
 * really in the write path. */
void dbg_palette(const void *colors,unsigned bytes);

/* Non-zero while a test pattern owns the panel, so DG_DrawFrame must leave the
 * displayed slot alone. */
int dbg_hold(void);

/* Frame-buffer blanking mode, read by I_StartFrame in i_video.c:
 * 1 (the default) black, 2 index 0xff, 0 off.
 *
 * Vanilla DOOM never clears I_VideoBuffer, so any pixel a frame does not paint
 * keeps the previous screen.  At 35 fps that lasts one tic and nobody notices;
 * at this port's 3 fps the first frames after a title/credit/demo transition -
 * which the engine paints only partly, measured 100 % unwritten at level tic 0
 * and a triangular hole at columns 0-60 rows 101-120 at tic 13 - sit on the
 * panel for a third of a second and read as colour blocks, bands and a flashing
 * title page.  Blanking costs one 64 KiB memset per frame, about 2 % of a frame
 * here, and removes the class of artifact.
 *
 * Mode 2 is the diagnostic: index 0xff is one the renderer never writes, so a
 * `g` dump names every pixel the frame left unwritten.  `c` cycles the modes. */
int dbg_clear_view(void);

/* Drain pending UART commands.  Called once per frame, and repeatedly while
 * frozen, where it also keeps BIOS_POLL running and drops key events. */
void dbg_service(void);

/* Same, from a real-time wait while dbg_held(): the frame in progress cannot
 * finish because the clock it is waiting on is frozen, so it is declared final
 * and the queued commands run.  Without this a pause taken inside a screen wipe
 * starves a queued frame dump forever. */
void dbg_service_held(void);

/* Geometry of the panel slots, set once by DG_Init and refreshed per flip.
 * displayed is the slot the panel is scanning right now. */
void dbg_video(uintptr_t fb0,uintptr_t fb1,unsigned stride,unsigned bytes_per_pixel,
	       unsigned width,unsigned height,unsigned displayed);

/* Where I_FinishUpdate put the game image inside the slot, plus the 8-bit game
 * buffer it converted from.  Reported from the i_video.c hook.  The offsets are
 * bytes: x_offset is the left margin, y_offset the byte distance from the slot
 * start to the first picture row (whole rows, since vertical centring skips
 * rows), x_offset_end the right margin.  The dumps crop with these. */
void dbg_blit(unsigned started_ms,int x_offset,int y_offset,int x_offset_end,
	      const void *game_buffer,unsigned game_w,unsigned game_h);

/* Non-zero while a dump's base64 body is being written.  Everything else that
 * wants the UART must stay silent, or its bytes land inside the dump.  Only the
 * body is exclusive: header and trailer are whole lines, junk around them is
 * skipped by the host parser. */
int dbg_uart_busy(void);

/* Shim file-I/O snapshot for the 'i' command: struct dbg_io and
 * shim_io_snapshot() live in shim_private.h, next to the counters. */
#endif
