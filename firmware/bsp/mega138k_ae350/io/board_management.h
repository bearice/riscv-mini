#pragma once
#include <stdint.h>
/* Polling, single owner. Flash entry points issue read commands only. */
enum { BUS_OK=0,BUS_DEVICE=-1,BUS_INVALID=-2,BUS_TIMEOUT=-3,BUS_NACK=-4,BUS_ARBITRATION=-5,BUS_PROTOCOL=-6 };
struct board_flash_info { uint32_t controller, jedec, bytes, status, sfdp, hz; };
int board_flash_init(struct board_flash_info *info);
int board_flash_read(uint32_t offset,void *buffer,unsigned bytes);
int board_i2c_init(void);
int board_i2c_probe(unsigned address);
int board_i2c_read_register(unsigned address,uint8_t offset,void *buffer,unsigned bytes);
int board_twi_mux_select(unsigned selection);
unsigned board_twi_mux_get(void);
int board_management_stop(void);
