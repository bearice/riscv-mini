#include "../bios/include/bios.h"
static void write_text(const char *s,unsigned n) {bios_call(BIOS_WRITE,(uintptr_t)s,n,0,0);}
static volatile uint32_t zeroed[16];
static volatile uint32_t initialized=0x12345678;
int payload_main(const struct bios_info *info) {
    static const char hello[]="PAYLOAD: resident BIOS ecall services active\r\n";
    write_text(hello,sizeof(hello)-1);
    struct bios_info queried;
    unsigned ok=1;
    for(unsigned i=0;i<16;++i)if(zeroed[i])ok=0;
    if(initialized!=0x12345678)ok=0;
    zeroed[0]=1;initialized=0; /* Next boot must reload .data and zero .bss. */
    if(bios_call(BIOS_INFO,(uintptr_t)&queried,0,0,0) || queried.version!=info->version)ok=0;
    if(bios_call(BIOS_INFO,0x40800000,0,0,0)!=-1 || bios_call(999,0,0,0,0)!=-1)ok=0;
    struct bios_io io;if(bios_call(BIOS_IO_READ,(uintptr_t)&io,0,0,0))ok=0;
    uint8_t buffer[512];static const char filename[64]="RVTEST00.BIN";
    if(queried.features&1u) {
        if(bios_call(BIOS_SD_READ,0,(uintptr_t)buffer,1,0) || bios_call(BIOS_FILE_READ,(uintptr_t)filename,0,(uintptr_t)buffer,4)!=4)ok=0;
    }
    if(bios_call(BIOS_FLASH_READ,0,(uintptr_t)buffer,4,0) || bios_call(BIOS_RGB,0,0,0,0))ok=0;
    if(!ok) {static const char fail[]="PAYLOAD DEMO FAIL\r\n";write_text(fail,sizeof(fail)-1);return 1;}
    bios_call(BIOS_LEDS,0x15,0,0,0);
    if(!bios_call(BIOS_VIDEO_MODE,BIOS_GRAPHICS,0,0,0)) {
        for(unsigned slot=0;slot<2;++slot) {
            volatile uint16_t *fb=(volatile uint16_t *)(uintptr_t)queried.framebuffer[slot];
            for(unsigned y=0;y<queried.height;++y) {
                for(unsigned x=0;x<queried.width;++x)fb[y*queried.width+x]=x<160?0xf800:x<320?0x07e0:0x001f;
                bios_call(BIOS_POLL,0,0,0,0);
            }
            bios_call(BIOS_VIDEO_PRESENT,slot,0,0,0);
        }
        unsigned start=bios_call(BIOS_TIME,0,0,0,0);
        while((uint32_t)(bios_call(BIOS_TIME,0,0,0,0)-start)<1000)bios_call(BIOS_POLL,0,0,0,0);
    }
    static const char done[]="PAYLOAD DEMO PASS\r\n";write_text(done,sizeof(done)-1);
    bios_call(BIOS_LEDS,0,0,0,0);bios_call(BIOS_VIDEO_MODE,BIOS_TEXT,0,0,0);return 0;
}
