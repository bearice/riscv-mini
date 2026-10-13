/* DOOM-specific observability hooks. Standard C functions come from Newlib. */
#include "runtime.h"
#include "shim_private.h"
#include "dg_debug.h"
#include <stdio.h>
static unsigned long io_bytes, io_crc=0xfffffffful;
static unsigned long next_report=256ul*1024ul;
unsigned long shim_crc32(unsigned long crc,const void *data,unsigned n) {
    const unsigned char *s=data;
    while(n--) {crc^=*s++;for(unsigned k=0;k<8;++k)crc=(crc>>1)^(0xEDB88320ul&-(crc&1ul));}
    return crc;
}
void bios_libc_read_hook(const void *data,unsigned n) {
    io_crc=shim_crc32(io_crc,data,n); io_bytes+=n;
}
void bios_libc_poll_hook(void) {
    shim_debug_hook();
    if(io_bytes>=next_report && !dbg_uart_busy()) {
        next_report=((io_bytes>>18)+1)<<18;
        struct bios_libc_io stats; bios_libc_io_snapshot(&stats);
        printf("libc: reads %u KiB fs=%u ms/%u calls poll=%u ms/%u crc=0x%08x\n",
               (unsigned)(io_bytes>>10),(unsigned)stats.fs_ms,(unsigned)stats.fs_calls,
               (unsigned)stats.poll_ms,(unsigned)stats.poll_calls,(unsigned)~io_crc);
    }
}
int bios_libc_console_busy(void) { return dbg_uart_busy(); }
void shim_io_snapshot(struct dbg_io *out) {
    struct bios_libc_io stats; bios_libc_io_snapshot(&stats);
    out->bytes=io_bytes; out->crc=~io_crc;
    out->fs_ms=stats.fs_ms; out->fs_calls=stats.fs_calls;
    out->poll_ms=stats.poll_ms; out->poll_calls=stats.poll_calls;
}
