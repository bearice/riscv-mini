#pragma once
#include <stdint.h>
/* ILP32 M-mode ecall interface. a7=magic, a6=function; a0..a5 arguments.
 * Negative return values are errors. Firmware retains mtvec and device ownership.
 * Payloads must poll; this cooperative interface is not SBI or a security boundary. */
#define BIOS_ABI_VERSION 1u
#define BIOS_ECALL_MAGIC 0x42494f53u
#define BIOS_PAYLOAD_BASE 0x41000000u
#define BIOS_PAYLOAD_LIMIT 0x47e00000u
enum bios_function {BIOS_INFO=0,BIOS_WRITE,BIOS_GETC,BIOS_TIME,BIOS_POLL,
    BIOS_VIDEO_MODE,BIOS_VIDEO_PRESENT,BIOS_SD_READ,BIOS_SD_WRITE,
    BIOS_FILE_READ,BIOS_IO_READ,BIOS_LEDS,BIOS_REBOOT,
    BIOS_FLASH_READ,BIOS_RGB,BIOS_MOUSE};
enum {BIOS_TEXT=0,BIOS_GRAPHICS=1};
struct bios_info {uint32_t version,size,ram_base,ram_bytes,payload_base,payload_limit,
    framebuffer[2],width,height,stride,format,features,clock_hz;};
struct bios_io {uint32_t buttons,switches;};
struct bios_mouse {uint32_t buttons;int32_t x,y,wheel;uint32_t time_ms;};
#if defined(__riscv)
static inline int32_t bios_call(unsigned fn,uintptr_t x0,uintptr_t x1,uintptr_t x2,uintptr_t x3) {
    register uintptr_t a0 __asm__("a0")=x0,a1 __asm__("a1")=x1;
    register uintptr_t a2 __asm__("a2")=x2,a3 __asm__("a3")=x3;
    register uintptr_t a6 __asm__("a6")=fn,a7 __asm__("a7")=BIOS_ECALL_MAGIC;
    __asm__ volatile("ecall":"+r"(a0),"+r"(a1),"+r"(a2),"+r"(a3):"r"(a6),"r"(a7):"a4","a5","memory");
    return (int32_t)a0;
}
#endif
