#pragma once
#include <stdint.h>
/* Single owner, polling. Call board_i2c_init() before reading and eventually
 * board_management_stop(). Does not change video enable/mode or assert HPD.
 * Bus errors retain BUS_* values; invalid data has separate errors below. */
enum { AE350_EDID_BLOCK_BYTES=128, AE350_EDID_HEADER=-7,
       AE350_EDID_CHECKSUM=-8, AE350_EDID_CAPACITY=-9,
       AE350_EDID_DISCONNECTED=-10 };
/* block 0..255; validates base header and every block checksum.
 * On failure buffer contents are unspecified. */
int ae350_edid_read_block(unsigned block,uint8_t buffer[128]);
/* Reads base + all advertised extensions. capacity is in bytes, >=128.
 * *blocks_read counts only validated blocks, including on error. A small
 * buffer returns CAPACITY after the base block, without reading extensions.
 * A failed/incomplete result must not be used for display negotiation. */
int ae350_edid_read(void *buffer,unsigned capacity,unsigned *blocks_read);
