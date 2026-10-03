#pragma once
#include "include/bios.h"
#define BIOS_IMAGE_MAGIC 0x31425052u /* RPB1 */
#define BIOS_IMAGE_MAX (4u*1024u*1024u)
struct bios_image {uint32_t magic,version,load,file_bytes,memory_bytes,entry,crc,header_crc;};
static inline uint32_t bios_crc(const void *p,unsigned n) {
    const uint8_t *s=p;uint32_t c=~0u;
    while(n--) {c^=*s++;for(unsigned i=0;i<8;++i)c=(c>>1)^(0xedb88320u&(0u-(c&1u)));}
    return ~c;
}
static inline int bios_header_valid(const struct bios_image *h,unsigned length) {
    return h->magic==BIOS_IMAGE_MAGIC && h->version==BIOS_ABI_VERSION &&
        h->load==BIOS_PAYLOAD_BASE && h->file_bytes>=4 && h->file_bytes<=BIOS_IMAGE_MAX &&
        length==sizeof(*h)+h->file_bytes && h->memory_bytes>=h->file_bytes &&
        h->memory_bytes<=BIOS_PAYLOAD_LIMIT-BIOS_PAYLOAD_BASE-65536u &&
        !(h->entry&3u) && h->entry>=h->load && h->entry-h->load<=h->file_bytes-4 &&
        h->header_crc==bios_crc(h,sizeof(*h)-4);
}
