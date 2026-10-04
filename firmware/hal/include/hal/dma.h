#pragma once
#include <generated/csr.h>

/* Native writers bypass L2. Call only after DMA completion, before CPU reads. */
static inline void hal_dma_invalidate(void) {
    __asm__ volatile("fence rw,rw":::"memory");
#ifdef CSR_L2_INVALIDATE_ADDR
    l2_invalidate_write(1);
    while(l2_busy_read()) {}
#endif
#if MINI_CPU_DCACHE
    __asm__ volatile(".word 0x0000500f":::"memory");
#endif
}
