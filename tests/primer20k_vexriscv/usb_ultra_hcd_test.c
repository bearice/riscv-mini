/* Run with native GCC and USB_ULTRA_TEST; register mock drives real HCD. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "host/hcd.h"
#include "host/usbh.h"
#include <hal/usb_ultra.h>
#include <hal/hal.h>
static uint32_t regs[9],now;
static tusb_speed_t speed=TUSB_SPEED_FULL;
bool tuh_bus_info_get(uint8_t device,tuh_bus_info_t *bus) {(void)device;memset(bus,0,sizeof(*bus));bus->speed=speed;return true;}
static uint8_t rx[64],tx[64];
static unsigned rxpos,txlen,events;
static hcd_event_t last;
uint32_t hal_time_ms(void) {return now;}
hal_result_t hal_irq_enable(unsigned source,unsigned enabled) {(void)source;(void)enabled;return HAL_OK;}
uint32_t rd(unsigned offset) {return offset==32?rx[rxpos++]:regs[offset/4];}
void wr(unsigned offset,uint32_t value) {
    if(offset==32) {tx[txlen++]=value;return;}
    if(offset==8) {regs[3]&=~value;return;}
    if(offset==0 && (value&256))txlen=0;
    regs[offset/4]=value;
    if(offset==24)regs[7]=1u<<31;
}
void hcd_event_handler(hcd_event_t const *event,bool isr) {(void)isr;last=*event;++events;}
static void tick(void) {++now;hcd_ultra_poll();}
static void response(unsigned pid,const uint8_t *data,unsigned len,unsigned errors) {
    if(data)memcpy(rx,data,len);rxpos=0;
    regs[7]=(1u<<28)|(pid<<16)|len|errors;regs[3]|=2;tick();
}
static void launch(void) {regs[7]=1u<<28;tick();assert(regs[7]>>31);}
int main(void) {
    tusb_rhport_init_t init={.role=TUSB_ROLE_HOST,.speed=TUSB_SPEED_FULL};
    assert(hcd_init(0,&init));assert(!(regs[0]&1));regs[1]=1;regs[7]=1u<<28;
    for(unsigned i=0;i<25;++i)tick();
    assert(last.event_id==HCD_EVENT_DEVICE_ATTACH && (regs[0]&1));
    tusb_desc_endpoint_t ep={.bLength=7,.bDescriptorType=TUSB_DESC_ENDPOINT,.bEndpointAddress=0,
                           .wMaxPacketSize=8};
    assert(hcd_edpt_open(0,0,&ep));
    uint8_t setup[8]={0x80,6},buffer[80]={0},data[8]={1,2,3,4,5,6,7,8};
    assert(hcd_setup_send(0,0,setup));launch();
    assert((regs[6]&(1u<<29)) && ((regs[6]>>16)&255)==0x2d);
    assert(txlen==8 && !memcmp(tx,setup,8));
    response(0xd2,0,0,0);assert(last.xfer_complete.result==XFER_RESULT_SUCCESS);
    assert(hcd_edpt_xfer(0,0,0x80,buffer,16));launch();
    assert(regs[6]&(1u<<28));response(0x5a,0,0,0);tick();
    assert(regs[7]>>31); // control NAK retries without completing
    response(0x4b,data,8,0);assert(buffer[0]==1 && (regs[7]>>31));
    // Duplicate DATA1 is ACKed by hardware, but not copied or toggled.
    unsigned before=events;response(0x4b,data,8,0);assert(events==before);tick();
    response(0xc3,data,3,0);assert(last.xfer_complete.len==11 && buffer[8]==1);
    assert(last.xfer_complete.result==XFER_RESULT_SUCCESS);
    assert(hcd_edpt_xfer(0,0,0,0,0));launch();response(0xd2,0,0,0);
    assert(last.xfer_complete.len==0);
    // Interrupt NAK is normal idle; STALL reports once and clearing resets DATA0.
    ep.bEndpointAddress=0x81;ep.bmAttributes.xfer=TUSB_XFER_INTERRUPT;ep.bInterval=4;ep.wMaxPacketSize=64;
    assert(hcd_edpt_open(0,1,&ep));assert(hcd_edpt_xfer(0,1,0x81,buffer,64));launch();
    before=events;response(0x5a,0,0,0);assert(events==before);
    for(unsigned i=0;i<4;++i)tick();assert(regs[7]>>31);
    response(0x1e,0,0,0);assert(last.xfer_complete.result==XFER_RESULT_STALLED);
    assert(hcd_edpt_clear_stall(0,1,0x81));assert(hcd_edpt_xfer(0,1,0x81,buffer,64));launch();
    // Three CRC errors produce one bounded failure, never copy corrupt bytes.
    before=events;
    for(unsigned i=0;i<3;++i) {response(0xc3,data,8,1u<<30);for(unsigned j=0;j<4;++j)tick();}
    assert(events==before+1 && last.xfer_complete.result==XFER_RESULT_FAILED);
    assert(hcd_ultra_validate(1));
    // Packet > requested capacity is rejected.
    assert(hcd_edpt_xfer(0,1,0x81,buffer,2));launch();response(0xc3,data,8,0);
    assert(last.xfer_complete.result==XFER_RESULT_FAILED);
    // A Hub and child may reuse an endpoint number; address ownership differs.
    assert(hcd_edpt_open(0,5,&ep));
    assert(hcd_edpt_xfer(0,5,0x81,buffer,8));launch();
    assert(((regs[6]>>9)&127)==5);response(0xc3,data,8,0);
    assert(last.dev_addr==5 && last.xfer_complete.result==XFER_RESULT_SUCCESS);
    hcd_device_close(0,5);
    assert(hcd_edpt_xfer(0,1,0x81,buffer,8));launch();response(0xc3,data,8,0);
    assert(last.dev_addr==1 && last.xfer_complete.result==XFER_RESULT_SUCCESS);
    // Reject unsupported speeds before any packet can be scheduled.
    speed=TUSB_SPEED_LOW;assert(!hcd_edpt_open(0,2,&ep));speed=TUSB_SPEED_FULL;
    // A failed EP0 pair reservation must leave the last free slot available.
    hcd_device_close(0,0);hcd_device_close(0,1);
    unsigned slots=2*(CFG_TUH_DEVICE_MAX+CFG_TUH_HUB+1)+2*CFG_TUH_HID+CFG_TUH_HUB;
    for(unsigned i=0;i<slots-1;++i)assert(hcd_edpt_open(0,i+1,&ep));
    tusb_desc_endpoint_t control=ep;control.bEndpointAddress=0;control.bmAttributes.xfer=TUSB_XFER_CONTROL;
    assert(!hcd_edpt_open(0,100,&control));assert(hcd_edpt_open(0,100,&ep));
    assert(!hcd_edpt_open(0,101,&ep));
    regs[1]=0;for(unsigned i=0;i<25;++i)tick();assert(last.event_id==HCD_EVENT_DEVICE_REMOVE);
    assert(!(regs[0]&1));
    regs[1]=1;for(unsigned i=0;i<25;++i)tick();
    assert(last.event_id==HCD_EVENT_DEVICE_ATTACH && (regs[0]&1));
    puts("PASS USB PIO HCD: setup, ACK, NAK, short packet, duplicate toggle, ZLP, STALL, CRC, bounds, Hub address isolation, speed rejection, pool exhaustion, removal, reattach");
}
