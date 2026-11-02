#pragma once
#include <stdint.h>

int ae350_net_init(const uint8_t *mac, const uint8_t *ip);
int ae350_tftp_get(const uint8_t server[4], const char *filename, void *dest, unsigned capacity, unsigned *out_len);
int ae350_ping(const uint8_t target[4], unsigned count);
uint32_t ae350_crc32(const void *buf, unsigned len);
