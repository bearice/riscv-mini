#pragma once
#include <stdint.h>
#define ETH_DMA_OWN 0x80000000u
#define ETH_DMA_DONE 0x40000000u
#define ETH_DMA_ERROR 0x20000000u
struct ethernet_descriptor {
    uint32_t buffer,capacity,length,flags,cookie,reserved[3];
} __attribute__((aligned(32)));
/* All ranges must be DDR, disjoint, and occupy exclusive cache lines. */
struct ethernet_ring_config {
    struct ethernet_descriptor *tx,*rx;
    uint8_t *tx_buffers,*rx_buffers;
    unsigned count,stride;
};
int ethernet_dma_init(const struct ethernet_ring_config *config);
int ethernet_dma_stop(void);
/* 1=borrowed, 0=not available, negative=error. One lease per direction.
 * Submit/release ends CPU ownership. Do not touch a device-owned cache line.
 * TX completion means copied into MAC storage, not transmission on the wire. */
int ethernet_dma_tx_acquire(uint8_t **buffer);
int ethernet_dma_tx_submit(unsigned bytes);
void ethernet_dma_tx_cancel(void);
int ethernet_dma_rx_acquire(const uint8_t **buffer,unsigned *bytes);
int ethernet_dma_rx_release(void);
uint32_t ethernet_packet_reg(unsigned offset);
