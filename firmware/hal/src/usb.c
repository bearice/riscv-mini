#include <hal/hal.h>
#include <generated/csr.h>
#include <generated/soc.h>
#include <generated/mem.h>
#include <string.h>
#include "tusb.h"
#include "host/hcd.h"
#if CONFIG_USB_ULTRA
#include <hal/usb_ultra.h>
#else
#include "portable/ohci/ohci.h"
#define OHCI ((volatile ohci_registers_t *)USB_OHCI_BASE)
#endif
static hal_usb_info_t info;
static unsigned running,polling;
static volatile unsigned irqs,controller_errors;
static hal_usb_key_t keys[32];static unsigned key_head,key_tail;
static hal_usb_report_t reports[8];static unsigned report_head,report_tail;
static hal_usb_mouse_t mice[32];static unsigned mouse_head,mouse_tail;
static struct {uint8_t previous[8],device,leds,led_pending;unsigned keyboard,mouse;} interfaces[4];
uint32_t tusb_time_millis_api(void) {return hal_time_ms();}
bool hcd_dcache_clean(void const *p,uint32_t n) {(void)p;(void)n;__asm__ volatile("fence rw,rw":::"memory");return true;}
bool hcd_dcache_invalidate(void const *p,uint32_t n) {(void)p;(void)n;__asm__ volatile("fence rw,rw":::"memory");return true;}
static void usb_irq(void *unused) {
    (void)unused;++irqs;
#if !CONFIG_USB_ULTRA
    if(OHCI->interrupt_status & OHCI->interrupt_enable & (1u<<4))++controller_errors;
#endif
    hcd_int_handler(0,true);
}
bool hcd_deinit(uint8_t port) {(void)port;usb_host_reset_write(1);return true;}
void hal_usb_stop(void) {
    running=0;hal_irq_enable(USB_HOST_INTERRUPT,0);
    if(tuh_inited()) {
        hcd_int_disable(0);
#if CONFIG_USB_ULTRA
        /* TinyUSB deinit still accesses HCD registers. Reset follows it. */
#else
        OHCI->interrupt_disable=0xffffffffu;
        OHCI->control=0; /* Stop scheduling before TinyUSB releases descriptors. */
#endif
        uint32_t deadline=hal_time_ms()+10;while(!hal_deadline_reached(hal_time_ms(),deadline)) {}
        tuh_deinit(0);
    }
    usb_host_reset_write(1);usb_host_enable_write(0);
    info.initialized=info.connected=info.hid_interfaces=0;
    key_head=key_tail=report_head=report_tail=mouse_head=mouse_tail=0;memset(interfaces,0,sizeof(interfaces));
}
hal_result_t hal_usb_init(void) {
    hal_usb_stop();usb_host_enable_write(1);
    uint32_t deadline=hal_time_ms()+150;
    while(!usb_host_ready_read()) {
        if(usb_host_error_read() || hal_deadline_reached(hal_time_ms(),deadline)) {++info.errors;return HAL_TIMEOUT;}
    }
    info.phy_id=usb_host_id_read();
    if(info.phy_id!=0x00060424u) {++info.errors;return HAL_IO;}
    usb_host_reset_write(0);
    deadline=hal_time_ms()+2;while(!hal_deadline_reached(hal_time_ms(),deadline)) {}
#if !CONFIG_USB_ULTRA
    if((OHCI->revision&255)!=0x10) {++info.errors;usb_host_reset_write(1);return HAL_IO;}
#endif
    hal_irq_attach(USB_HOST_INTERRUPT,usb_irq,0);
    tuh_hid_set_default_protocol(HID_PROTOCOL_BOOT);
    if(!tuh_init(0)) {++info.errors;hal_usb_stop();return HAL_IO;}
    info.initialized=running=1;return HAL_OK;
}
void hal_usb_poll(void) {
    if(!running || polling)return;
    polling=1;
#if CONFIG_USB_ULTRA
    hcd_ultra_poll();
#endif
    tuh_task();polling=0;
}
void hal_usb_get_info(hal_usb_info_t *out) {
    if(!out)return;
    info.phy_ready=usb_host_ready_read();info.phy_error=usb_host_error_read();info.lines=usb_host_lines_read();info.phy_id=usb_host_id_read();
    if(running) {
#if CONFIG_USB_ULTRA
        info.control=hcd_ultra_control();info.port_status=hcd_ultra_status();info.frame=hcd_frame_number(0);info.hcca=0;
#else
        info.control=OHCI->control;info.port_status=OHCI->rhport_status[0];info.frame=OHCI->frame_number;info.hcca=OHCI->hcca;
#endif
    }
    unsigned state=hal_irq_save();*out=info;out->irqs=irqs;out->errors+=controller_errors;hal_irq_restore(state);
}
hal_result_t hal_usb_key_take(hal_usb_key_t *out) {
    if(!out)return HAL_INVALID;
    if(key_head==key_tail)return HAL_BUSY;
    *out=keys[(key_tail++)&31];return HAL_OK;
}
hal_result_t hal_usb_report_take(hal_usb_report_t *out) {
    if(!out)return HAL_INVALID;
    if(report_head==report_tail)return HAL_BUSY;
    *out=reports[(report_tail++)&7];return HAL_OK;
}
hal_result_t hal_usb_mouse_take(hal_usb_mouse_t *out) {
    if(!out)return HAL_INVALID;
    if(mouse_head==mouse_tail)return HAL_BUSY;
    *out=mice[(mouse_tail++)&31];return HAL_OK;
}
hal_result_t hal_usb_keyboard_leds(uint8_t device,uint8_t itf,uint8_t leds) {
    if(itf>=4 || leds>31)return HAL_INVALID;
    if(!running || interfaces[itf].device!=device)return HAL_NO_MEDIA;
    if(!interfaces[itf].keyboard)return HAL_UNSUPPORTED;
    if(interfaces[itf].led_pending)return HAL_BUSY;
    interfaces[itf].leds=leds;
    interfaces[itf].led_pending=1;
    if(tuh_hid_set_report(device,itf,0,HID_REPORT_TYPE_OUTPUT,&interfaces[itf].leds,1))return HAL_OK;
    interfaces[itf].led_pending=0;return HAL_BUSY;
}
static void key_event(uint8_t device,uint8_t itf,uint8_t usage,unsigned down,uint8_t modifiers) {
    if(key_head-key_tail==32) {++info.key_drops;return;}
    keys[(key_head++)&31]=(hal_usb_key_t){hal_time_ms(),device,itf,usage,(uint8_t)down,modifiers};
    ++info.key_events;
}
void tuh_mount_cb(uint8_t device) {
    info.connected=1;info.device=device;tuh_vid_pid_get(device,&info.vid,&info.pid);++info.mounts;
    tuh_bus_info_t bus;tuh_bus_info_get(device,&bus);info.speed=bus.speed;
}
void tuh_umount_cb(uint8_t device) {(void)device;info.connected=0;++info.unmounts;}
void tuh_hid_mount_cb(uint8_t device,uint8_t itf,uint8_t const *desc,uint16_t length) {
    (void)desc;(void)length;
    if(itf>=4) {++info.errors;return;}
    interfaces[itf].keyboard=tuh_hid_interface_protocol(device,itf)==HID_ITF_PROTOCOL_KEYBOARD;
    interfaces[itf].mouse=tuh_hid_interface_protocol(device,itf)==HID_ITF_PROTOCOL_MOUSE;
    interfaces[itf].device=device;
    ++info.hid_interfaces;
    if(!tuh_hid_receive_report(device,itf))++info.errors;
}
void tuh_hid_umount_cb(uint8_t device,uint8_t itf) {
    if(itf<4) {
        uint8_t *old=interfaces[itf].previous;
        for(unsigned i=2;i<8;++i)if(old[i]>3)key_event(device,itf,old[i],0,0);
        for(unsigned i=0;i<8;++i)if(old[0]&(1u<<i))key_event(device,itf,0xe0+i,0,0);
        memset(&interfaces[itf],0,sizeof(interfaces[itf]));
    }
    if(info.hid_interfaces)--info.hid_interfaces;
}
static unsigned has_key(const uint8_t *report,unsigned key) {
    for(unsigned i=2;i<8;++i)if(report[i]==key)return 1;
    return 0;
}
void tuh_hid_report_received_cb(uint8_t device,uint8_t itf,uint8_t const *data,uint16_t length) {
    ++info.reports;
    if(length>64 || report_head-report_tail==8)++info.report_drops;
    else {hal_usb_report_t *r=&reports[(report_head++)&7];r->device=device;r->interface=itf;r->length=length;memcpy(r->data,data,length);}
    if(itf<4 && interfaces[itf].keyboard && tuh_hid_get_protocol(device,itf)==HID_PROTOCOL_BOOT && length==8) {
        uint8_t *old=interfaces[itf].previous;unsigned rollover=0;
        for(unsigned i=2;i<8;++i)if(data[i]>=1 && data[i]<=3)rollover=1;
        if(rollover)++info.rollovers;
        else {
            for(unsigned i=0;i<8;++i)if((old[0]^data[0])&(1u<<i))key_event(device,itf,0xe0+i,!!(data[0]&(1u<<i)),data[0]);
            for(unsigned i=2;i<8;++i)if(old[i]>3 && !has_key(data,old[i]))key_event(device,itf,old[i],0,data[0]);
            for(unsigned i=2;i<8;++i)if(data[i]>3 && !has_key(old,data[i]))key_event(device,itf,data[i],1,data[0]);
            memcpy(old,data,8);
        }
    }
    if(itf<4 && interfaces[itf].mouse && tuh_hid_get_protocol(device,itf)==HID_PROTOCOL_BOOT && length>=3) {
        if(mouse_head-mouse_tail==32)++info.mouse_drops;
        else {mice[(mouse_head++)&31]=(hal_usb_mouse_t){hal_time_ms(),device,itf,data[0],(int8_t)data[1],(int8_t)data[2],length>3?(int8_t)data[3]:0};++info.mouse_events;}
    }
    if(!tuh_hid_receive_report(device,itf))++info.errors;
}
void tuh_hid_set_report_complete_cb(uint8_t device,uint8_t itf,uint8_t id,uint8_t type,uint16_t length) {
    (void)id;(void)type;
    if(itf<4 && interfaces[itf].device==device)interfaces[itf].led_pending=0;
    if(length!=1)++info.errors;
}
