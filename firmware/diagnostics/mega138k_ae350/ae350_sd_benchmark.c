/* Read-only application-API throughput. Timing includes SD commands, DMAC,
 * cache maintenance and the bounce copy. CRC and UART are outside rdcycle. */
#include <stdint.h>
#include "ae350_sd.h"
#include "ae350_address_map.h"
#ifndef SD_BENCH_CACHE
#define SD_BENCH_CACHE 0
#endif
#ifndef SD_BENCH_HZ
#define SD_BENCH_HZ 25000000u
#endif
#ifndef SD_BENCH_REFERENCE_CRC
#define SD_BENCH_REFERENCE_CRC 0u
#endif
#define BYTES (1u<<20)
#define FIRST_LBA 2048u
#define BASE 0x02000000u
#define ILM __attribute__((section(".ilm.text")))
static volatile uint32_t *const r=(volatile uint32_t *)AE350_RESULTS_BASE;
static ILM void putc(char c) {
    while(!(*(volatile uint32_t *)0xf0300034u&0x20u)) {}
    *(volatile uint32_t *)0xf0300020u=(uint8_t)c;
}
static ILM void puts(const char *s) {while(*s)putc(*s++);}
static ILM void hex(uint32_t value) {for(int n=28;n>=0;n-=4)putc("0123456789abcdef"[(value>>n)&15]);}
static inline uint64_t cycles(void) {
    uint32_t hi,lo,last;
    do {__asm__ volatile("rdcycleh %0":"=r"(hi));__asm__ volatile("rdcycle %0":"=r"(lo));__asm__ volatile("rdcycleh %0":"=r"(last));}while(hi!=last);
    return ((uint64_t)hi<<32)|lo;
}
static ILM __attribute__((noinline)) uint32_t crc32(const uint8_t *p) {
    uint32_t c=~0u;for(unsigned n=0;n<BYTES;n++) {c^=p[n];for(unsigned b=0;b<8;b++)c=(c>>1)^(0xedb88320u&-(c&1));}return ~c;
}
static ILM int measure(void) {
    uint8_t *const data=(uint8_t *)BASE;
    volatile uint32_t *const before=(volatile uint32_t *)(BASE-32);
    volatile uint32_t *const after=(volatile uint32_t *)(BASE+BYTES);
    uint32_t reference=0;
    for(unsigned group=0;group<3;group++) {
        unsigned sectors=group==0?1:(group==1?8:128);
        for(unsigned rep=0;rep<3;rep++) {
            unsigned index=group*3+rep;
            *before=*after=0x1234abcd;
            puts("SD_BENCH START ");hex(index);puts("\r\n");
            uint64_t start=cycles();
            int error=0;
            for(unsigned block=0;block<BYTES/512;block+=sectors) {
                error=ae350_sd_read(FIRST_LBA+block,data+block*512,sectors);
                if(error)break;
            }
            uint64_t elapsed=cycles()-start;
            puts("SD_BENCH END ");hex(index);putc(' ');hex((uint32_t)elapsed);putc(' ');hex(elapsed>>32);puts("\r\n");
            uint32_t crc=crc32(data);
            if(index==0)reference=crc;
            unsigned guards=*before==0x1234abcd && *after==0x1234abcd;
            puts("SD_BENCH CHECK ");hex(index);putc(' ');hex(crc);putc(' ');hex(error);putc(' ');hex(guards);puts("\r\n");
            if(error || !guards || crc!=reference || (SD_BENCH_REFERENCE_CRC && crc!=SD_BENCH_REFERENCE_CRC))return -1;
        }
    }
    return 0;
}
void main(void) {
    for(unsigned i=0;i<32;i++)r[i]=0;
    r[0]=0x53420001;
    if(SD_BENCH_CACHE) {
        uint32_t enable=SD_BENCH_CACHE;
        __asm__ volatile("csrs 0x7ca,%0; fence.i"::"r"(enable):"memory");
    }
    uint32_t cache;__asm__ volatile("csrr %0,0x7ca":"=r"(cache));
    int rc=ae350_sd_init(2);
    if(!rc)rc=ae350_sd_set_clock(SD_BENCH_HZ);
    struct ae350_sd_info info;ae350_sd_info(&info);
    r[1]=rc;r[2]=info.hz;r[3]=info.width;r[4]=AE350_CPU_HZ;r[5]=cache;
    r[8]=info.command;r[9]=info.response;r[10]=info.error;
    puts("SD_BENCH CONFIG ");hex(info.hz);putc(' ');hex(info.width);putc(' ');hex(AE350_CPU_HZ);putc(' ');hex(cache);puts("\r\n");
    if(!rc && info.sectors>FIRST_LBA+BYTES/512)rc=measure();else rc=-1;
    ae350_sd_info(&info);r[11]=info.command;r[12]=info.response;r[13]=info.error;r[14]=info.reads;
    r[6]=rc;r[7]=ae350_sd_stop();
    r[0]=0x5342ffff;
    puts("SD_BENCH DONE\r\n");
    for(;;)__asm__ volatile("wfi");
}
