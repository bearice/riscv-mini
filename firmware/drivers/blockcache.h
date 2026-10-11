#pragma once
#include <stdint.h>
/* Sector read cache that sits in front of a disk driver's raw transport.
 *
 * The win is per sector, not per call: FatFs re-reads FAT and directory sectors
 * on every open/seek, and payload file reads are small and scattered.  A cache
 * block is 4 KiB because that is the largest single command the native SD host
 * issues, so a miss costs exactly the command that would have been issued
 * anyway and the rest of the block is free read-ahead.
 *
 * Writes go to the card first (write-through) and the cached copy is refreshed
 * afterwards, so no dirty data exists: nothing has to be flushed before a
 * reset, a card removal or a failed command, and a failed write simply leaves
 * the old bytes cached-and-on-card.
 */
enum {
    BC_SECTORS = 8u,        /* sectors per cache block, i.e. 4 KiB */
    BC_BYTES = 512u*BC_SECTORS,
    /* 128 KiB.  Bigger was measured and is not free: 512 blocks (2 MiB) left
     * the same 1007 FS_READ calls for the same 1282 KiB of DOOM WAD traffic
     * (the call count is bounded by the caller's 4 KiB coalescing, not by the
     * card), cost ~250 ms more in the read path (the LRU scan is linear), and
     * did not change the panel's `rgb_lcd underflows` count (148 vs 137 over a
     * comparable run).  The underruns track CPU memory traffic, not SD reads:
     * see docs/doom.md.  The BIOS region is 4 MiB at 0x00800000 and its
     * text+rodata+data is ~120 KiB, so this is 128 KiB of a 4 MiB budget.
     */
    BC_BLOCKS = 32u
};
/* Fill one aligned block (BC_SECTORS sectors at block*BC_SECTORS); 0 on
 * success.  A driver may clamp a short card tail: bytes past the last sector
 * are never handed out, because every request is bounds-checked first. */
typedef int (*bc_fill)(uint32_t block,void *dst);
void bc_invalidate(void);                   /* media change or controller re-init */
int bc_enabled(void);                       /* 0 => callers must use the raw path */
void bc_set_enabled(int enabled);           /* raw-speed benchmarks bypass the cache */
void bc_stats(uint32_t *hits,uint32_t *misses,uint32_t *evictions);
int bc_read(uint32_t sector,uint8_t *dst,uint32_t count,bc_fill fill);
/* Call after a successful raw write so a cached copy stays coherent. */
void bc_written(uint32_t sector,const uint8_t *src,uint32_t count);
