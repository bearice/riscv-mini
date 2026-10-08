/* XIP boot code must never hold the software SPI CS across instruction fetches.
 * The hardware wakes the NOR; all boot reads use its memory-mapped window.
 * Installing images is deliberately unavailable while executing from Flash.
 */
#include "flash.h"
#include "../common/memory_layout.h"
unsigned flash_size(void) { return MINI_FLASH_SIZE; }
int flash_init(uint32_t *id) { if(id)*id=0;return 1; }
int flash_read(unsigned address,void *data,unsigned length) {
    if(address>=MINI_FLASH_SIZE || length>MINI_FLASH_SIZE-address)return 0;
    const volatile unsigned char *source=(const volatile unsigned char *)(MINI_XIP_BASE+address);
    unsigned char *dest=data;
    while(length--)*dest++=*source++;
    return 1;
}
int flash_erase_sector(unsigned address) { (void)address;return 0; }
int flash_program(unsigned address,const void *data,unsigned length) {
    (void)address;(void)data;(void)length;return 0;
}
