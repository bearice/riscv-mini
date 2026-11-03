#pragma once
#include <stdint.h>

int ae350_net_init(const uint8_t *mac, const uint8_t *ip);
/* Stops ring DMA; required before memory leaves BIOS ownership. */
int ae350_net_stop(void);
int ae350_tftp_get(const uint8_t server[4], const char *filename, void *dest, unsigned capacity, unsigned *out_len);
int ae350_ping(const uint8_t target[4], unsigned count);
uint32_t ae350_crc32(const void *buf, unsigned len);
/* zlib-style continuation: ae350_crc32_update(ae350_crc32(a), b) == crc32(a || b). */
uint32_t ae350_crc32_update(uint32_t previous, const void *buf, unsigned len);
