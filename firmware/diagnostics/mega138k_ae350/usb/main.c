/* Polling USB Host qualification; UART and results survive missing devices. */
#include <stdint.h>
#include <stdbool.h>
#include <stdarg.h>
#include "ae350_address_map.h"
#include "usb_platform.h"
#include "tusb.h"
#include "host/hcd.h"

#define W(a) (*(volatile uint32_t *)(uintptr_t)(a))
static volatile uint32_t *const result=(volatile uint32_t *)AE350_RESULTS_BASE;
static uint64_t start;
static uint64_t cycles(void) {
    uint32_t hi,lo,again;
    do {__asm__ volatile("rdcycleh %0":"=r"(hi));__asm__ volatile("rdcycle %0":"=r"(lo));__asm__ volatile("rdcycleh %0":"=r"(again));}while(hi!=again);
    return ((uint64_t)hi<<32)|lo;
}
uint32_t hal_time_ms(void) {return (uint32_t)((cycles()-start)/800000u);}
uint32_t tusb_time_millis_api(void) {return hal_time_ms();}
static void out(const char *s) {while(*s) {while(!(W((AE350_UART_BASE+0x34u))&0x20u)){} W((AE350_UART_BASE+0x20u))=(uint8_t)*s++;}}
static void hex(uint32_t v) {for(int n=28;n>=0;n-=4) {char b[2]={"0123456789abcdef"[(v>>n)&15],0};out(b);}}
#if CFG_TUSB_DEBUG
/* TinyUSB's optional trace uses UART directly; no BIOS/newlib dependency. */
int usb_debug_printf(const char *format, ...) {
    va_list args;va_start(args,format);
    while(*format) {
        if(*format!='%') {char b[2]={*format++,0};out(b);continue;}
        format++;
        if(*format=='%') {out("%");format++;continue;}
        unsigned width=0;char pad=' ';
        if(*format=='0') {pad='0';format++;}
        while(*format>='0' && *format<='9')width=width*10+(*format++-'0');
        bool is_long=*format=='l';if(is_long)format++;
        char kind=*format++;
        if(kind=='s') {const char *s=va_arg(args,const char *);out(s?s:"(null)");continue;}
        if(kind=='c') {char b[2]={(char)va_arg(args,int),0};out(b);continue;}
        if(kind=='p') {hex((uint32_t)(uintptr_t)va_arg(args,void *));continue;}
        bool signed_value=kind=='d' || kind=='i';
        uint32_t value=signed_value?(is_long?(uint32_t)va_arg(args,long):(uint32_t)va_arg(args,int)):
                                   (is_long?(uint32_t)va_arg(args,unsigned long):va_arg(args,unsigned));
        if(signed_value && (int32_t)value<0) {out("-");value=0u-value;}
        unsigned base=(kind=='x' || kind=='X')?16:10,n=0;
        const char *digits=kind=='X'?"0123456789ABCDEF":"0123456789abcdef";
        char buffer[33];do {buffer[n++]=digits[value%base];value/=base;}while(value);
        while(width>n) {char b[2]={pad,0};out(b);width--;}
        while(n) {char b[2]={buffer[--n],0};out(b);}
    }
    va_end(args);return 0;
}
#endif
static void topology(void) {
    uint32_t mounted=0;
    for(unsigned d=1;d<=CFG_TUH_DEVICE_MAX+CFG_TUH_HUB;d++) {
        uint16_t vid,pid;
        if(tuh_mounted(d))mounted|=1u<<d;
        if(tuh_vid_pid_get(d,&vid,&pid) && !(result[22]&(1u<<d))) {
            result[22]|=1u<<d;
            out("USB NODE ");hex(d);out(" ");hex(((uint32_t)vid<<16)|pid);out("\r\n");
        }
    }
    if(mounted!=result[21]) {result[21]=mounted;out("USB MOUNTED ");hex(mounted);out("\r\n");}
}
#if USB_TEST_MSC
static uint8_t msc_address;
static volatile unsigned msc_state;
static uint8_t sectors[2][576] __attribute__((aligned(32)));
static uint32_t sector_crc(const uint8_t *p) {
    uint32_t c=~0u;
    for(unsigned i=0;i<512;i++) {c^=p[i];for(unsigned b=0;b<8;b++)c=(c>>1)^(0xedb88320u&-(c&1));}
    return ~c;
}
static bool sector_done(uint8_t dev,const tuh_msc_complete_data_t *done) {
    (void)dev;unsigned n=(unsigned)done->user_arg;
    if(done->csw->status) {msc_state=9;out("USB MSC READ FAIL\r\n");return true;}
    uint8_t guard=n?0x5a:0xa5;
    for(unsigned i=0;i<32;i++)if(sectors[n][i]!=guard || sectors[n][544+i]!=guard) {
        msc_state=9;out("USB MSC GUARD FAIL\r\n");return true;
    }
    result[12+n]=sector_crc(sectors[n]+32);result[14]++;
    if(!n)msc_state=3;
    else if(result[12]==result[13]) {
        result[11]=1;msc_state=5;
        out("USB MSC READONLY PASS ");hex(result[12]);out(" ");hex(result[13]);out("\r\n");
    } else {msc_state=9;out("USB MSC CRC FAIL\r\n");}
    return true;
}
void tuh_msc_mount_cb(uint8_t dev) {
    if(msc_address)return;
    msc_address=dev;result[15]=tuh_msc_get_block_count(dev,0);result[16]=tuh_msc_get_block_size(dev,0);
    msc_state=(result[15] && result[16]==512)?1:9;
}
void tuh_msc_umount_cb(uint8_t dev) {if(dev==msc_address)msc_state=9;}
#endif
bool hcd_deinit(uint8_t port) {(void)port;W(USB_HOST_BASE+4)=0;return true;}
bool hcd_dcache_clean(const void *p,uint32_t n) {(void)p;(void)n;return true;}
bool hcd_dcache_invalidate(const void *p,uint32_t n) {(void)p;(void)n;return true;}
bool hcd_dcache_clean_invalidate(const void *p,uint32_t n) {(void)p;(void)n;return true;}
void tuh_mount_cb(uint8_t dev) {uint16_t vid,pid;tuh_vid_pid_get(dev,&vid,&pid);result[3]++;result[4]=((uint32_t)vid<<16)|pid;out("USB DEVICE ");hex(result[4]);out("\r\n");}
void tuh_umount_cb(uint8_t dev) {(void)dev;result[6]++;if(result[3])result[3]--;out("USB DETACH\r\n");}
void tuh_hid_mount_cb(uint8_t dev,uint8_t itf,const uint8_t *desc,uint16_t len) {(void)desc;(void)len;result[5]++;out("USB HID\r\n");tuh_hid_receive_report(dev,itf);}
void tuh_hid_umount_cb(uint8_t dev,uint8_t itf) {(void)dev;(void)itf;if(result[5])result[5]--;}
void tuh_hid_report_received_cb(uint8_t dev,uint8_t itf,const uint8_t *p,uint16_t len) {result[7]++;result[8]=len;result[9]=0;for(unsigned i=0;i<len && i<4;i++)result[9]|=(uint32_t)p[i]<<(i*8);out("USB REPORT ");hex(result[9]);out("\r\n");tuh_hid_receive_report(dev,itf);}
void main(void) {
    for(unsigned i=0;i<32;i++)result[i]=0;
    start=cycles();result[0]=0x55534201;out("USB BEGIN\r\n");
    #if USB_TEST_MSC
    out("USB MSC BEGIN\r\n");
    for(unsigned i=0;i<576;i++) {sectors[0][i]=0xa5;sectors[1][i]=0x5a;}
    #endif
    if(W(USB_HOST_BASE+0x100)!=0xae350c02u) {out("USB ID FAIL\r\nUSB ENUM FAIL\r\n");for(;;){}}
    while(!(W(USB_HOST_BASE+0x104)&8u) && hal_time_ms()<500u) {}
    if(!(W(USB_HOST_BASE+0x104)&8u)) {out("USB CLOCK FAIL\r\nUSB ENUM FAIL\r\n");for(;;){}}
    tuh_hid_set_default_protocol(HID_PROTOCOL_BOOT);
    result[1]=tuh_init(0);if(!result[1]) {out("USB INIT FAIL\r\n");for(;;){}}
    #if USB_RESET_PROBE
    W(USB_HOST_BASE+0x54)=0x10;
    unsigned probe_end=hal_time_ms()+30,seen_reset=0;
    while(hal_time_ms()<probe_end) {
        unsigned raw=W(USB_HOST_BASE+0x104);
        if((raw&15u)==12u)seen_reset|=1;
        if(W(USB_HOST_BASE+0x54)&16u)seen_reset|=2;
    }
    result[24]=seen_reset;result[25]=W(USB_HOST_BASE+0x54);result[26]=W(USB_HOST_BASE+0x104);
    out("USB RESET PROBE ");hex(result[24]);out(" ");hex(result[25]);out(" ");hex(result[26]);out("\r\n");
    #endif
    unsigned last=0;
    while(hal_time_ms()<60000u) {
        hcd_int_handler(0,false);tuh_task();
        #if USB_TEST_MSC
        if((msc_state==1 || msc_state==3) && tuh_msc_ready(msc_address)) {
            unsigned n=msc_state==3;msc_state=n?4:2;
            if(!tuh_msc_read10(msc_address,0,sectors[n]+32,0,1,sector_done,n))msc_state=9;
        }
        #endif
        unsigned sec=hal_time_ms()/1000u;
        if(sec!=last)topology();
        if(sec!=last) {last=sec;result[10]=W(USB_HOST_BASE+0x0c);out("USB STATUS ");hex(result[10]);out(" CTRL ");hex(W(USB_HOST_BASE+4));out(" FRAME ");hex(W(USB_HOST_BASE+0x3c));out(" PORT ");hex(W(USB_HOST_BASE+0x54));out(" RAW ");hex(W(USB_HOST_BASE+0x104));out(" VIDPID ");hex(result[4]);out(" HID ");hex(result[5]);out(" REPORTS ");hex(result[7]);out("\r\n");}
    }
    result[2]=result[3] && result[5];
    #if USB_TEST_MSC
    result[2]=result[2] && msc_state==5;
    #endif
    result[31]=0x555342ff;
    out(result[2]?"USB ENUM PASS\r\n":"USB ENUM FAIL\r\n");for(;;)__asm__ volatile("wfi");
}
