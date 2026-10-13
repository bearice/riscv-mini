/* UART-loaded diagnostic for the loader's packet copy + whole-image CRC path.
 * No Flash writes. Scratch DDR is private while this diagnostic owns the CPU. */
#include "io.h"
#include "image.h"
#include <generated/csr.h>

static const uint8_t packet[128]={
    0xa5,0x80,0xef,0xca,0x31,0x1c,0x7b,0xa6,0x8c,0xe9,0xd6,0x33,0x18,0x45,0xa2,0x8f,
    0xf7,0xd2,0x3d,0x18,0x43,0xae,0x89,0xf4,0xde,0x3b,0x64,0x41,0xaa,0x97,0xf0,0xdd,
    0x01,0x64,0x4b,0xae,0x95,0xf8,0xdf,0x02,0x68,0x4d,0xb2,0x97,0xfc,0x21,0x06,0x6b,
    0x53,0xb6,0x99,0xfc,0x27,0x0a,0x6d,0x50,0xba,0x9f,0xc0,0x25,0x0e,0x73,0x54,0xb9,
    0xed,0xc8,0x27,0x02,0x79,0x54,0xb3,0xee,0xc4,0x21,0x1e,0x7b,0x50,0x8d,0xea,0xc7,
    0x3f,0x1a,0x75,0x50,0x8b,0xe6,0xc1,0x3c,0x16,0x73,0xac,0x89,0xe2,0xdf,0x38,0x15,
    0x49,0xac,0x83,0xe6,0xdd,0x30,0x17,0x4a,0xa0,0x85,0xfa,0xdf,0x34,0x69,0x4e,0xa3,
    0x9b,0xfe,0xd1,0x34,0x6f,0x42,0xa5,0x98,0xf2,0xd7,0x08,0x6d,0x46,0xbb,0x9c,0xf1,
};
static const unsigned sizes[]={1024,4096,65536};
static const uint32_t checks[]={0x820db3e8u,0x9b4591fcu,0xd72f6ab9u};
static volatile uint8_t *const bytes=(volatile uint8_t *)0x02000000u;

static unsigned check(unsigned n,uint32_t expected,const char *kind) {
    unsigned bad=n;
    for(unsigned i=0;i<n;++i) if(bytes[i]!=packet[i&127u]) {bad=i;break;}
    uint32_t crc=image_crc(bytes,n);
    puts_uart("COPYCRC ");puts_uart(kind);puts_uart(" size=");io_hex(n);
    puts_uart(" crc=");io_hex(crc);puts_uart(" expected=");io_hex(expected);
    puts_uart(" first_bad=");io_hex(bad);
    if(bad<n) {puts_uart(" got=");io_hex(bytes[bad]);puts_uart(" wanted=");io_hex(packet[bad&127u]);}
    unsigned ok=bad==n && crc==expected;
    puts_uart(ok?" PASS\r\n":" FAIL\r\n");return ok;
}

int main(void) {
    unsigned ok=1;
    uint32_t crc=image_crc(packet,sizeof(packet));
    puts_uart("COPYCRC packet crc=");io_hex(crc);puts_uart(" expected=51c405ca\r\n");
    ok&=crc==0x51c405cau;
    for(unsigned k=0;k<3;++k) {
        unsigned n=sizes[k];
        /* Match the bootloader's volatile byte copy, including its fence. */
        for(unsigned i=0;i<n;++i)bytes[i]=packet[i&127u];
        __asm__ volatile("fence rw,rw":::"memory");
        ok&=check(n,checks[k],"byte");
        /* Distinguish byte-lane handling from general memory/CRC failure. */
        volatile uint32_t *words=(volatile uint32_t *)(uintptr_t)bytes;
        for(unsigned i=0;i<n;i+=4) {
            unsigned at=i&127u;
            words[i/4]=(uint32_t)packet[at]|((uint32_t)packet[at+1]<<8)
                |((uint32_t)packet[at+2]<<16)|((uint32_t)packet[at+3]<<24);
        }
        __asm__ volatile("fence rw,rw":::"memory");
        ok&=check(n,checks[k],"word");
    }
    puts_uart(ok?"COPYCRC ALL PASS\r\n":"COPYCRC ALL FAIL\r\n");
    puts_uart("SYSTEM READY\r\nSTATE=app_ready stage=copycrc abi=" MINI_IMAGE_ABI_HEX " id=" MINI_APP_ID "\r\n> ");
    for(;;)if(!uart_rxempty_read()) {
        unsigned ch=uart_rxtx_read();uart_ev_pending_write(2);
        if(ch=='!') {ctrl_reset_write(1);for(;;){}}
    }
}
