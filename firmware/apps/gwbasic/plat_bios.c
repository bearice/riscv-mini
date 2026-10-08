/* plat_bios.c - the BIOS ecall back end for the GW-BASIC payload.
 *
 * The payload runs under the resident BIOS: console I/O, the clock, the RGB
 * panel and SD file reads are ecall services (a7 = BIOS_ECALL_MAGIC, a6 = the
 * function number, a0..a5 = arguments, result in a0, negative = error).
 * Everything the interpreter core needs is here; the core itself never touches
 * an ecall. */
#include "basic.h"
#include "../../bios/include/bios.h"

#define BIOS_ECALL_MAGIC_ 0x42494f53u  /* "BIOS" — also in bios.h */

enum {
    SVC_INFO = 0, SVC_WRITE = 1, SVC_GETC = 2, SVC_TIME = 3, SVC_POLL = 4,
    SVC_VIDEO_MODE = 5, SVC_VIDEO_PRESENT = 6, SVC_SD_READ = 7,
    SVC_SD_WRITE = 8, SVC_FILE_READ = 9, SVC_IO_READ = 10, SVC_LEDS = 11,
    SVC_REBOOT = 12, SVC_FLASH_READ = 13, SVC_RGB = 14, SVC_MOUSE = 15,
};

static int ecall(int fn, uint32_t a0, uint32_t a1, uint32_t a2, uint32_t a3,
                 uint32_t a4, uint32_t a5)
{
    register uint32_t v0 asm("a0") = a0;
    register uint32_t v1 asm("a1") = a1;
    register uint32_t v2 asm("a2") = a2;
    register uint32_t v3 asm("a3") = a3;
    register uint32_t v4 asm("a4") = a4;
    register uint32_t v5 asm("a5") = a5;
    register uint32_t v6 asm("a6") = (uint32_t)fn;
    register uint32_t v7 asm("a7") = BIOS_ECALL_MAGIC;

    asm volatile("ecall"
                 : "+r"(v0), "+r"(v1), "+r"(v2), "+r"(v3), "+r"(v4), "+r"(v5)
                 : "r"(v6), "r"(v7)
                 : "memory");
    return (int)v0;
}

/* The BIOS description handed to payload_main. */
static struct bios_info info;
static int              have_info;

void bas_plat_set_info(const struct bios_info *src)
{
    info = *src;
    have_info = 1;
}

/* ------------------------------------------------------------------- console */

void bas_plat_putc(char c)
{
    char b = c;

    (void)ecall(SVC_WRITE, (uint32_t)(uintptr_t)&b, 1, 0, 0, 0, 0);
}

void bas_plat_flush(void)
{
    /* The BIOS console is unbuffered; nothing to drain. */
}

void bas_plat_poll(void)
{
    (void)ecall(SVC_POLL, 0, 0, 0, 0, 0, 0);
}

uint32_t bas_plat_ms(void)
{
    return (uint32_t)ecall(SVC_TIME, 0, 0, 0, 0, 0, 0);
}

int bas_plat_getc(void)
{
    return ecall(SVC_GETC, 0, 0, 0, 0, 0, 0);
}

/* ---------------------------------------------------------------- exit/reboot */

/* SYSTEM leaves the payload: returning from payload_main makes the BIOS print
 * "PAYLOAD RETURNED" and take over again.  main.c arms the jump target. */
static bas_jmp_buf exit_jmp;

int bas_plat_exit_arm(void)
{
    return bas_setjmp(&exit_jmp);
}

void bas_plat_exit(void)
{
    bas_longjmp(&exit_jmp, 1);
    /* The arm call was never reached: hand control back to the BIOS. */
    for (;;)
        (void)ecall(SVC_REBOOT, 0, 0, 0, 0, 0, 0);
}

/* --------------------------------------------------------------------- video */

int bas_plat_video(int graphics)
{
    if (!have_info)
        return -1;
    return ecall(SVC_VIDEO_MODE, (uint32_t)graphics, 0, 0, 0, 0, 0);
}

uint16_t *bas_plat_framebuffer(int *width, int *height, int *stride)
{
    int slot;

    if (!have_info || info.framebuffer[0] == 0)
        return 0;
    /* Double buffered: draw into the back buffer and swap with PRESENT. */
    slot = (info.framebuffer[1] != 0) ? 1 : 0;
    *width = (int)info.width;
    *height = (int)info.height;
    /* The BIOS reports stride in bytes; the payload works in pixels. */
    *stride = (int)(info.stride / 2);
    return (uint16_t *)(uintptr_t)info.framebuffer[slot];
}

void bas_plat_present(void)
{
    (void)ecall(SVC_VIDEO_PRESENT, 0, 0, 0, 0, 0, 0);
}

/* ---------------------------------------------------------------------- files */

int bas_plat_file_read(const char *name, uint32_t offset, void *dst,
                       uint32_t capacity)
{
    if (!have_info)
        return -1;
    return ecall(SVC_FILE_READ, (uint32_t)(uintptr_t)name, offset,
                 (uint32_t)(uintptr_t)dst, capacity, 0, 0);
}

/* ------------------------------------------------------------- I/O ports */

/* The board has no I/O port space: INP answers "no device" and OUT is ignored.
 * The BIOS statement gives direct access to the services instead. */
void bas_plat_out(int port, int value)
{
    (void)port;
    (void)value;
}

int bas_plat_in(int port)
{
    (void)port;
    return -1;
}

void bas_bios_call(int n, const uint32_t *args)
{
    (void)ecall(n, args[0], args[1], args[2], args[3], args[4], args[5]);
}

/* bas_setjmp and bas_longjmp live in setjmp.S. */
