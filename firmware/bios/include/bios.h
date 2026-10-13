#pragma once
#include <stdint.h>
#include "../../common/memory_layout.h"
/* ILP32 M-mode ecall interface. a7=magic, a6=function; a0..a5 arguments.
 * Negative return values are errors. Firmware retains mtvec and device ownership.
 * Payloads must poll; this cooperative interface is not SBI or a security boundary. */
#define BIOS_ABI_VERSION 1u
#define BIOS_ECALL_MAGIC 0x42494f53u
#define BIOS_PAYLOAD_BASE 0x01000000u
#define BIOS_PAYLOAD_LIMIT 0x07e00000u
enum bios_function {BIOS_INFO=0,BIOS_WRITE,BIOS_GETC,BIOS_TIME,BIOS_POLL,
    BIOS_VIDEO_MODE,BIOS_VIDEO_PRESENT,BIOS_SD_READ,BIOS_SD_WRITE,
    BIOS_FILE_READ,BIOS_IO_READ,BIOS_LEDS,BIOS_REBOOT,
    BIOS_FLASH_READ,BIOS_RGB,BIOS_MOUSE,
    BIOS_AUDIO_BEGIN,BIOS_AUDIO_WRITE,BIOS_AUDIO_CONTROL,BIOS_AUDIO_INFO,
    BIOS_KEY,BIOS_FILE_SIZE,
    /* File system (SD through FatFs).  Handles are small non-negative integers
     * owned by the BIOS; the mount is implicit on first use.  See struct
     * bios_fs_stat and the BIOS_FS_* flags below. */
    BIOS_FS_OPEN,BIOS_FS_CLOSE,BIOS_FS_READ,BIOS_FS_WRITE,BIOS_FS_SEEK,
    BIOS_FS_STAT,BIOS_FS_MKDIR,BIOS_FS_UNLINK,BIOS_FS_RENAME,
    /* INDEX8: palette(pointer,bank,256), readback(pointer,bank,256),
     * atomic present(slot,bank), window(x0,y0,x1,y1), exclusive ends.
     * Features bit 5 advertises these append-only ABI v1 services. */
    BIOS_VIDEO_PALETTE,BIOS_VIDEO_PRESENT_INDEX8,BIOS_VIDEO_PALETTE_READ,
    BIOS_VIDEO_WINDOW};

/* BIOS_FS_OPEN flags (open(2)-style, distinct from the service ids above).
 * O_READ/O_WRITE select access; O_CREATE makes a new file and fails if it
 * exists, O_TRUNC creates-or-truncates, O_APPEND starts at the end.  Without
 * O_WRITE the handle is read-only. */
enum bios_fs_flag {BIOS_FS_O_READ=1u,BIOS_FS_O_WRITE=2u,BIOS_FS_O_CREATE=4u,
    BIOS_FS_O_TRUNC=8u,BIOS_FS_O_APPEND=16u};
/* BIOS_FS_SEEK whence, as in lseek(2). */
enum bios_fs_whence {BIOS_FS_SET=0,BIOS_FS_CUR=1,BIOS_FS_END=2};
/* BIOS_FS_STAT output: byte size and FatFs attribute bits (AM_DIR=0x10). */
struct bios_fs_stat {uint32_t size,attrib;};

enum bios_audio_command {BIOS_AUDIO_STOP=0,BIOS_AUDIO_PLAY,BIOS_AUDIO_PAUSE,
    BIOS_AUDIO_MUTE,BIOS_AUDIO_UNMUTE};
/* AUDIO_BEGIN: payload-owned aligned uint32_t ring, 1..65535 stereo frames.
 * AUDIO_WRITE: packed L16/R16 PCM, at most 1024 frames; returns accepted count.
 * CONTROL: bios_audio_command. INFO: writable struct bios_audio pointer.
 * Services are append-only ABI v1 additions; features bit 4 advertises audio. */
struct bios_audio {uint32_t sample_rate,level,played,fetched,underruns,overruns,errors;};
enum {BIOS_TEXT=0,BIOS_GRAPHICS=1,BIOS_GRAPHICS_INDEX8=2};
struct bios_info {uint32_t version,size,ram_base,ram_bytes,payload_base,payload_limit,
    framebuffer[2],width,height,stride,format,features,clock_hz;};
struct bios_io {uint32_t buttons,switches;};
struct bios_mouse {uint32_t buttons;int32_t x,y,wheel;uint32_t time_ms;};
/* KEY: raw USB HID keyboard events (usage page 0x07, including modifiers
 * E0..E7). pressed=1 keydown / 0 keyup; typematic repeat re-emits keydown.
 * This is the game-input path; the TTY queue (GETC) stays UART/console-only.
 * Return 0 on success, -1 when the queue is empty. */
struct bios_key {uint16_t usage,pressed;uint32_t modifiers,time_ms;};
#if defined(__riscv)
static inline int32_t bios_call(unsigned fn,uintptr_t x0,uintptr_t x1,uintptr_t x2,uintptr_t x3) {
    register uintptr_t a0 __asm__("a0")=x0,a1 __asm__("a1")=x1;
    register uintptr_t a2 __asm__("a2")=x2,a3 __asm__("a3")=x3;
    register uintptr_t a6 __asm__("a6")=fn,a7 __asm__("a7")=BIOS_ECALL_MAGIC;
    __asm__ volatile("ecall":"+r"(a0),"+r"(a1),"+r"(a2),"+r"(a3):"r"(a6),"r"(a7):"a4","a5","memory");
    return (int32_t)a0;
}
#endif
