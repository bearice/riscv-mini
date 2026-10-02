/* UART-only boot: no SPI Flash hardware and no MMIO accesses. */
#include "flash.h"
unsigned flash_size(void) {return 0;}
int flash_init(uint32_t *id) {if(id)*id=0;return 0;}
int flash_uid(uint8_t uid[16],unsigned *length) {(void)uid;if(length)*length=0;return 0;}
int flash_read(unsigned address,void *data,unsigned length) {(void)address;(void)data;(void)length;return 0;}
int flash_program(unsigned address,const void *data,unsigned length) {(void)address;(void)data;(void)length;return 0;}
int flash_erase_sector(unsigned address) {(void)address;return 0;}
