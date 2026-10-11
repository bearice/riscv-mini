/* Internal interface between shim.c and the platform/debug layers. */
#ifndef SHIM_PRIVATE_H
#define SHIM_PRIVATE_H
/* Guard frame: shim_setjmp() captures the CALLER's frame (it is asm), so call it
 * directly in the function you want exit() to return to; it returns 0 on save
 * and nonzero when exit() unwinds back.  shim_guard_arm() then enables exit(). */
int shim_setjmp(void);
void shim_guard_arm(void);

/* IEEE CRC32 (reflected, 0xEDB88320), seeded with ~0.  One implementation for
 * both the frame dumps and the file-read integrity check below. */
unsigned long shim_crc32(unsigned long crc,const void *data,unsigned n);

/* File-I/O counters for the debug console's 'i' command.  crc is over every
 * byte fread has returned: the same shim runs under QEMU, so a board value that
 * differs from the simulator's is a corrupted read, not a rendering fault. */
struct dbg_io {
    unsigned long bytes,fs_ms,fs_calls,poll_ms,poll_calls,crc;
};
void shim_io_snapshot(struct dbg_io *io);

/* Defined by the debug layer (dg_debug.c) as "service the console".  The shim
 * calls it from its throttled pump so the console answers during a long tick
 * (one startup tick reads 1.8 MiB of WAD); a hook keeps the shim independent
 * of the debugger. */
void shim_debug_hook(void);
#endif
