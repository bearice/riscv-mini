#pragma once
#include <stdint.h>

/* Shared ATCDMAC300 submit path. Error numbers are the DMA suite contract
 * (scripts/mini_ops/ae350_dma_contract.py). A caller-supplied device, ABI,
 * MMIO base or IRQ id that does not match the published table is rejected
 * here, before any channel register is written.
 */
enum {
    AE350_DMA_OK = 0,
    AE350_DMA_ERR_ALIGN = 1,
    AE350_DMA_ERR_SIZE = 2,
    AE350_DMA_ERR_REACH = 3,
    AE350_DMA_ERR_CAP = 4,
    AE350_DMA_ERR_DEVICE = 5,
    AE350_DMA_ERR_BIND = 6,
    AE350_DMA_ERR_ABI = 7,
    AE350_DMA_ERR_TIMEOUT = 8,
    AE350_DMA_ERR_DRAIN = 9,
    AE350_DMA_ERR_WIDTH = 10
};

#define AE350_DMA_LINE 32u

struct ae350_dma_req {
    uint32_t device;
    uint32_t abi;
    uint32_t csr_base;
    uint32_t irq_id;
    uint32_t need_cap;
    uint32_t channel;
    uint32_t src;
    uint32_t dst;
    uint32_t bytes;
    uint32_t width_bytes;
    uint32_t src_mode;
    uint32_t dst_mode;
    uint32_t handshake;
    uint32_t llp;
};

int ae350_dma_submit(const struct ae350_dma_req *req);
int ae350_dma_wait(uint32_t channel);
int ae350_dma_abort_drain(uint32_t channel);
uint32_t ae350_dma_busy(uint32_t channel);
void ae350_dma_reset(void);
int ae350_dma_sync_for_device(uint32_t addr, uint32_t bytes);
int ae350_dma_sync_for_cpu(uint32_t addr, uint32_t bytes);
