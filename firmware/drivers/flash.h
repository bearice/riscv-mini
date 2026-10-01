#pragma once
#include <stdint.h>
#define MINI_FLASH_SIZE 0x400000u
#define MINI_FLASH_WRITABLE 0x200000u
unsigned flash_size(void);
int flash_init(uint32_t *id);
int flash_uid(uint8_t uid[16],unsigned *length);
int flash_read(unsigned address, void *data, unsigned length);
int flash_program(unsigned address, const void *data, unsigned length);
int flash_erase_sector(unsigned address);
