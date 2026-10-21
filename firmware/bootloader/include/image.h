#pragma once
#include <stdint.h>
#include "image_abi.h"
#define IMAGE_MAGIC 0x354d5652u
#define IMAGE_VERSION 1u
#define FLASH_BYTES 0x400000u
#define IMAGE_OFFSET 0x200000u
#define APP_BASE 0x00800000u
#define IMAGE_CHUNK 128u
struct image_header {
    uint32_t magic, version, header_bytes, abi, length, load, entry, crc;
    uint32_t flags, reserved0, reserved1, header_crc;
};
_Static_assert(sizeof(struct image_header)==48, "Image ABI size");
static inline uint32_t crc_update(uint32_t crc, uint8_t value) {
    crc^=value;
    for(unsigned i=0;i<8;++i) crc=(crc>>1)^(0xedb88320u & (0u-(crc&1u)));
    return crc;
}
static inline uint32_t image_crc(const volatile void *data, unsigned length) {
    const volatile uint8_t *p=data;
    uint32_t crc=0xffffffffu;
    while(length--) crc=crc_update(crc,*p++);
    return ~crc;
}
static inline int image_valid(const struct image_header *h) {
    return h->magic==IMAGE_MAGIC && h->version==IMAGE_VERSION && h->header_bytes==sizeof(*h)
        && h->abi==MINI_IMAGE_ABI && !h->flags && !h->reserved0 && !h->reserved1
        && h->length>=4 && h->length<=FLASH_BYTES-IMAGE_OFFSET-sizeof(*h)
        && h->load==APP_BASE && !(h->entry&3u) && h->entry>=APP_BASE
        && h->entry-APP_BASE<=h->length-4
        && h->header_crc==image_crc(h,sizeof(*h)-4);
}
