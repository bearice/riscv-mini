/* Host test: clang -O2 tests/bios_crc_test.c -o bios_crc_test */
#include "../firmware/bios/payload.h"
#include <assert.h>
#include <stdio.h>
static uint32_t reference(const uint8_t *p,unsigned n) {
    uint32_t c=~0u;
    while(n--) {c^=*p++;for(unsigned i=0;i<8;++i)c=(c>>1)^(0xedb88320u&(0u-(c&1u)));}
    return ~c;
}
int main(void) {
    static uint8_t data[131073];uint32_t random=123456789;
    assert(bios_crc("123456789",9)==0xcbf43926u);
    assert(bios_crc(data,0)==0);
    for(unsigned i=0;i<sizeof(data);++i) {random=random*1664525u+1013904223u;data[i]=random>>24;}
    const unsigned sizes[]={1,31,32,255,256,511,512,65535,65536,65537,131073};
    for(unsigned i=0;i<sizeof(sizes)/sizeof(sizes[0]);++i) {
        unsigned n=sizes[i];assert(bios_crc(data,n)==reference(data,n));
        unsigned split=n/2;assert(~bios_crc_update(bios_crc_update(~0u,data,split),data+split,n-split)==reference(data,n));
        assert(bios_crc(data+1,n-1)==reference(data+1,n-1));
    }
    puts("BIOS CRC compatibility PASS");return 0;
}
