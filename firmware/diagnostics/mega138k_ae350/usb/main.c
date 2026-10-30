/* Polling USB Host qualification; UART and results survive missing devices. */
#include <stdint.h>
#include <stdbool.h>
#include "ae350_address_map.h"
#include "usb_platform.h"
#include "tusb.h"
#include "host/hcd.h"
void hcd_ultra_poll(void);
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
static void out(const char *s) {while(*s) {while(!(W(0xf0300034u)&0x20u)){} W(0xf0300020u)=(uint8_t)*s++;}}
static void hex(uint32_t v) {for(int n=28;n>=0;n-=4) {char b[2]={"0123456789abcdef"[(v>>n)&15],0};out(b);}}
bool hcd_deinit(uint8_t port) {(void)port;W(USB_PIO_BASE)=0;return true;}
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
    if(W(USB_PIO_BASE+0x100)!=0xae350c01u) {out("USB ID FAIL\r\nUSB ENUM FAIL\r\n");for(;;){}}
    while(!(W(USB_PIO_BASE+0x104)&8u) && hal_time_ms()<500u) {}
    if(!(W(USB_PIO_BASE+0x104)&8u)) {out("USB CLOCK FAIL\r\nUSB ENUM FAIL\r\n");for(;;){}}
    tuh_hid_set_default_protocol(HID_PROTOCOL_BOOT);
    result[1]=tuh_init(0);if(!result[1]) {out("USB INIT FAIL\r\n");for(;;){}}
    unsigned last=0;
    while(hal_time_ms()<60000u) {
        hcd_int_handler(0,false);hcd_ultra_poll();tuh_task();
        unsigned sec=hal_time_ms()/1000u;
        if(sec!=last) {last=sec;result[10]=W(USB_PIO_BASE+4);out("USB STATUS ");hex(result[10]);out(" CTRL ");hex(W(USB_PIO_BASE));out(" RAW ");hex(W(USB_PIO_BASE+0x104));out(" VIDPID ");hex(result[4]);out(" HID ");hex(result[5]);out(" REPORTS ");hex(result[7]);out("\r\n");}
    }
    result[2]=result[3] && result[5];result[31]=0x555342ff;
    out(result[2]?"USB ENUM PASS\r\n":"USB ENUM FAIL\r\n");for(;;)__asm__ volatile("wfi");
}
