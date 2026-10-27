#pragma once
#include <stdint.h>
/* Exclusive use of the selected DMAC channel; read/write backend.
 * Buffers may be unaligned: private cache-line-aligned DDR bounce storage owns
 * every DMA byte until completion or a successful abort/drain. */
struct ae350_sd_info { uint32_t sectors, hz, width, reads, error, command, response; };
int ae350_sd_init(unsigned channel);
int ae350_sd_set_clock(unsigned hz);
int ae350_sd_read(uint32_t sector, void *buffer, unsigned count);
int ae350_sd_write(uint32_t sector,const void *buffer,unsigned count);
int ae350_sd_sync(void);
int ae350_sd_status(void);
int ae350_sd_stop(void);
void ae350_sd_info(struct ae350_sd_info *info);
