/* See blockcache.h.  Fully associative with a linear scan over 32 entries: a
 * lookup costs tens of cycles next to a 4 KiB SD command, and it avoids the
 * stride aliasing a direct-mapped index would give 512-byte-aligned accesses.
 * Single-threaded by construction: the BIOS, its payloads and FatFs all run to
 * completion inside one poll loop, so no lock is needed (and an ecall from a
 * payload cannot interrupt a BIOS file operation). */
#include "blockcache.h"
#include <string.h>

static struct {uint32_t block,age;uint8_t valid;} slots[BC_BLOCKS];
static _Alignas(16) uint8_t store[BC_BLOCKS][BC_BYTES];
static uint32_t tick,hits,misses,evictions;
static int enabled=1;

void bc_invalidate(void) {memset(slots,0,sizeof slots);tick=0;}
int bc_enabled(void) {return enabled;}
void bc_set_enabled(int value) {enabled=value;if(!value)bc_invalidate();}
void bc_stats(uint32_t *hit_out,uint32_t *miss_out,uint32_t *evict_out) {
    if(hit_out) *hit_out=hits;
    if(miss_out) *miss_out=misses;
    if(evict_out) *evict_out=evictions;
}
static int slot_of(uint32_t block) {
    for(unsigned i=0;i<BC_BLOCKS;++i) if(slots[i].valid && slots[i].block==block) return (int)i;
    return -1;
}
static int victim_of(void) {
    unsigned best=0;
    for(unsigned i=0;i<BC_BLOCKS;++i) {
        if(!slots[i].valid) return (int)i;
        if(slots[i].age<slots[best].age) best=i;
    }
    return (int)best;
}
/* Bytes taken from a block never cross a block boundary, so `take` is always a
 * whole number of sectors and the loop below advances in exact multiples. */
static unsigned span(uint32_t sector,uint32_t count) {
    unsigned take=BC_BYTES-(unsigned)(sector%BC_SECTORS)*512u;
    if(count<BC_SECTORS) {
        unsigned want=count*512u;
        if(want<take) take=want;
    }
    return take;
}
int bc_read(uint32_t sector,uint8_t *dst,uint32_t count,bc_fill fill) {
    while(count) {
        uint32_t block=sector/BC_SECTORS;
        unsigned off=(unsigned)(sector%BC_SECTORS)*512u,take=span(sector,count);
        int slot=slot_of(block);
        if(slot<0) {
            slot=victim_of();
            if(slots[slot].valid) ++evictions;
            slots[slot].valid=0;              /* a failed fill must not stay visible */
            if(fill(block,store[slot])) return -1;
            slots[slot].block=block;slots[slot].valid=1;++misses;
        } else ++hits;
        slots[slot].age=++tick;
        memcpy(dst,&store[slot][off],take);
        dst+=take;sector+=take/512u;count-=take/512u;
    }
    return 0;
}
void bc_written(uint32_t sector,const uint8_t *src,uint32_t count) {
    while(count) {
        uint32_t block=sector/BC_SECTORS;
        unsigned off=(unsigned)(sector%BC_SECTORS)*512u,take=span(sector,count);
        int slot=slot_of(block);
        if(slot>=0) {memcpy(&store[slot][off],src,take);slots[slot].age=++tick;}
        src+=take;sector+=take/512u;count-=take/512u;
    }
}
