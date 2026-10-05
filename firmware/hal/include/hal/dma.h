#pragma once
#include <generated/csr.h>
#include <generated/soc.h>

/* Call after DMA completion, before CPU reads. Shared L2 writers maintain
 * its lines; CPU private L1 still needs a flush. */
static inline void hal_dma_invalidate(void) {
    __asm__ volatile("fence rw,rw":::"memory");
#if MINI_CPU_DCACHE
    __asm__ volatile(".word 0x0000500f":::"memory");
#endif
}
