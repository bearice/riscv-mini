#pragma once
#include "../bios/include/bios.h"
#include <stddef.h>

/* Single foreground app. Run once per payload load; exit() returns its status
 * to this guard after Newlib has run atexit callbacks and flushed stdio. */
int bios_libc_run(const struct bios_info *info,
                  int (*main_fn)(const struct bios_info *));
struct bios_libc_io { unsigned long fs_ms, fs_calls, poll_ms, poll_calls; };
void bios_libc_io_snapshot(struct bios_libc_io *out);
/* Optional app hooks. No libc calls from the read hook (stdio is locked).
 * The poll hook may service a debugger; a recursion guard protects it. */
void bios_libc_poll_hook(void);
void bios_libc_read_hook(const void *data, unsigned size);
int bios_libc_console_busy(void);
