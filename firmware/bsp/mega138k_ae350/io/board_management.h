#pragma once
#include <stdint.h>
/* Polling, single owner. Quad array reads; volatile QE restored on stop. */
enum { BUS_OK=0,BUS_DEVICE=-1,BUS_INVALID=-2,BUS_TIMEOUT=-3,BUS_NACK=-4,BUS_ARBITRATION=-5,BUS_PROTOCOL=-6 };
struct board_flash_info { uint32_t controller, jedec, bytes, status, sfdp, hz; };
/* status packs SR1, original SR2 and active Quad SR2 in bytes 0/1/2. */
int board_flash_init(struct board_flash_info *info);
int board_flash_read(uint32_t offset,void *buffer,unsigned bytes);
int board_flash_read_single(uint32_t offset,void *buffer,unsigned bytes);
int board_i2c_init(void);
int board_i2c_probe(unsigned address);
int board_i2c_read_register(unsigned address,uint8_t offset,void *buffer,unsigned bytes);
/* Initialized bus, single owner. E-DDC segment + offset + repeated START.
 * Reads exactly 128 bytes, selects TX DDC (raw mux 3), restores old mux.
 * No segment-pointer write for blocks 0/1 (legacy DDC compatibility). */
int board_hdmi_edid_read_block(unsigned block,void *buffer);
int board_twi_mux_select(unsigned selection);
unsigned board_twi_mux_get(void);
int board_management_stop(void);
