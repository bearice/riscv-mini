/* TinyUSB HCD for the Ultraembedded PIO full-speed host. No DMA or heap. */
#include <hal/hal.h>
#include <hal/usb_ultra.h>
#include <generated/mem.h>
#include <generated/soc.h>
#include "host/hcd.h"
#include "host/usbh.h"
#include <string.h>

enum {CTRL=0,STATUS=4,IRQ_ACK=8,IRQ_STS=12,IRQ_MASK=16,
      TX_LEN=20,TOKEN=24,RX_STAT=28,FIFO=32};
enum {FS_CTRL=0xe9,RESET_CTRL=0xc4,IRQ_SOF=1,IRQ_DONE=2,IRQ_ERR=4,
      PID_SETUP=0x2d,PID_OUT=0xe1,PID_IN=0x69,
      PID_ACK=0xd2,PID_NAK=0x5a,PID_STALL=0x1e,PID_DATA0=0xc3,PID_DATA1=0x4b};
#ifndef USB_ULTRA_TEST
static uint32_t rd(unsigned reg) {return *(volatile uint32_t *)(USB_PIO_BASE+reg);}
static void wr(unsigned reg,uint32_t v) {*(volatile uint32_t *)(USB_PIO_BASE+reg)=v;}
#else
extern uint32_t rd(unsigned reg);
extern void wr(unsigned reg,uint32_t value);
#endif
typedef struct {
    uint8_t used,dev,ep,mps,type,interval,toggle,pending,setup,retries;
    uint16_t length,done;
    uint8_t *buffer;
    uint32_t next,deadline;
} endpoint_t;
/* Control pairs (including address zero), HID IN/OUT and Hub status IN. */
enum {ENDPOINT_COUNT=2*(CFG_TUH_DEVICE_MAX+CFG_TUH_HUB+1)+2*CFG_TUH_HID+CFG_TUH_HUB};
static endpoint_t endpoints[ENDPOINT_COUNT];
static uint8_t setup_data[8];
static int active=-1;
static unsigned cursor,packet_length,initialized,attached,resetting;
static uint32_t detect_since,disconnect_since,launched;
static volatile uint32_t frames;
static unsigned due(uint32_t now,uint32_t deadline) {return (int32_t)(now-deadline)>=0;}
static endpoint_t *find(uint8_t dev,uint8_t ep) {
    for(unsigned i=0;i<ENDPOINT_COUNT;++i)if(endpoints[i].used && endpoints[i].dev==dev && endpoints[i].ep==ep)return &endpoints[i];
    return 0;
}
static void finish(endpoint_t *e,xfer_result_t result) {
    e->pending=0;active=-1;
    hcd_event_xfer_complete(e->dev,e->ep,e->done,result,false);
}
static void retry(endpoint_t *e,uint32_t now,unsigned error) {
    active=-1;
    if(error && ++e->retries>=3) {finish(e,XFER_RESULT_FAILED);return;}
    if(e->type!=TUSB_XFER_INTERRUPT && due(now,e->deadline)) {finish(e,XFER_RESULT_FAILED);return;}
    e->next=now+(e->type==TUSB_XFER_INTERRUPT?e->interval:1);
}
bool hcd_init(uint8_t port,const tusb_rhport_init_t *init) {
    (void)port;(void)init;
    memset(endpoints,0,sizeof(endpoints));active=-1;cursor=0;frames=0;
    attached=resetting=detect_since=disconnect_since=0;initialized=1;
    /* Keep SOF off until attach debounce: own SOF K/SE0 symbols would
       otherwise restart the initial continuous-J debounce every millisecond. */
    wr(IRQ_MASK,0);wr(IRQ_ACK,15);wr(CTRL,(FS_CTRL&~1u)|256);return true;
}
void hcd_int_enable(uint8_t port) {(void)port;wr(IRQ_MASK,IRQ_SOF);hal_irq_enable(USB_HOST_INTERRUPT,1);}
void hcd_int_disable(uint8_t port) {(void)port;hal_irq_enable(USB_HOST_INTERRUPT,0);wr(IRQ_MASK,0);}
void hcd_int_handler(uint8_t port,bool in_isr) {
    (void)port;(void)in_isr;
    uint32_t status=rd(IRQ_STS);
    if(status&IRQ_SOF) {++frames;wr(IRQ_ACK,IRQ_SOF);}
    /* DONE/ERR are polled: SOF also sets DONE, so ownership and SIE idle
       must be checked before completing a submitted packet. */
}
uint32_t hcd_frame_number(uint8_t port) {(void)port;return frames;}
bool hcd_port_connect_status(uint8_t port) {(void)port;return resetting?attached:((rd(STATUS)&3)==1);}
tusb_speed_t hcd_port_speed_get(uint8_t port) {(void)port;return TUSB_SPEED_FULL;}
void hcd_port_reset(uint8_t port) {(void)port;resetting=1;active=-1;wr(CTRL,RESET_CTRL);}
void hcd_port_reset_end(uint8_t port) {(void)port;wr(CTRL,FS_CTRL|256);resetting=0;}
bool hcd_edpt_open(uint8_t port,uint8_t dev,tusb_desc_endpoint_t const *desc) {
    (void)port;
    tuh_bus_info_t bus;tuh_bus_info_get(dev,&bus);
    /* This serial PHY has no low-speed/PRE or high-speed packet mode. */
    if(bus.speed!=TUSB_SPEED_FULL)return false;
    unsigned mps=tu_edpt_packet_size(desc);
    if(!mps || mps>64 || desc->bmAttributes.xfer==TUSB_XFER_ISOCHRONOUS)return false;
    endpoint_t *e=find(dev,desc->bEndpointAddress);
    if(!e)for(unsigned i=0;i<ENDPOINT_COUNT;++i)if(!endpoints[i].used) {e=&endpoints[i];break;}
    if(!e || e->pending)return false;
    /* Reserve both control directions before changing either slot. */
    endpoint_t *other=0;
    if(!(desc->bEndpointAddress&15)) {
        other=find(dev,0x80);
        if(!other)for(unsigned i=0;i<ENDPOINT_COUNT;++i)
            if(!endpoints[i].used && &endpoints[i]!=e) {other=&endpoints[i];break;}
        if(!other || other->pending)return false;
    }
    *e=(endpoint_t){.used=1,.dev=dev,.ep=desc->bEndpointAddress,.mps=mps,
                   .type=desc->bmAttributes.xfer,.interval=desc->bInterval?desc->bInterval:1};
    if(other) {*other=*e;other->ep=0x80;}
    return true;
}
bool hcd_edpt_close(uint8_t port,uint8_t dev,uint8_t ep) {
    (void)port;endpoint_t *e=find(dev,ep);
    if(!e)return false;
    if(active==(int)(e-endpoints))return false;
    memset(e,0,sizeof(*e));return true;
}
void hcd_device_close(uint8_t port,uint8_t dev) {
    (void)port;
    for(unsigned i=0;i<ENDPOINT_COUNT;++i)if(endpoints[i].used && endpoints[i].dev==dev) {
        if(active==(int)i)active=-1;
        memset(&endpoints[i],0,sizeof(endpoints[i]));
    }
}
bool hcd_edpt_xfer(uint8_t port,uint8_t dev,uint8_t ep,uint8_t *buffer,uint16_t length) {
    (void)port;endpoint_t *e=find(dev,ep);
    if(!e || e->pending || (length && !buffer))return false;
    e->pending=1;e->setup=0;e->buffer=buffer;e->length=length;e->done=0;e->retries=0;
    e->next=hal_time_ms();e->deadline=e->next+500;return true;
}
bool hcd_setup_send(uint8_t port,uint8_t dev,uint8_t const setup[8]) {
    endpoint_t *out=find(dev,0),*in=find(dev,0x80);
    if(!out || !in || out->pending || in->pending)return false;
    memcpy(setup_data,setup,8);
    if(!hcd_edpt_xfer(port,dev,0,setup_data,8))return false;
    out->setup=1;out->toggle=0;in->toggle=1;return true;
}
bool hcd_edpt_abort_xfer(uint8_t port,uint8_t dev,uint8_t ep) {
    (void)port;endpoint_t *e=find(dev,ep);
    if(!e || !e->pending || active==(int)(e-endpoints))return false;
    e->pending=0;return true;
}
bool hcd_edpt_clear_stall(uint8_t port,uint8_t dev,uint8_t ep) {
    (void)port;endpoint_t *e=find(dev,ep);if(!e)return false;e->toggle=0;return true;
}
void hcd_ultra_poll(void) {
    if(!initialized)return;
    uint32_t now=hal_time_ms();
    if(!resetting) {
        unsigned connected=(rd(STATUS)&3)==1;
        if(connected) {
            disconnect_since=0;
            if(!detect_since)detect_since=now?now:1;
            if(!attached && due(now,detect_since+20)) {attached=1;wr(CTRL,FS_CTRL);hcd_event_device_attach(0,false);}
        } else {
            detect_since=0;
            /* Packets include SE0; require 20ms of observed absence. */
            if(!disconnect_since)disconnect_since=now?now:1;
            if(attached && due(now,disconnect_since+20)) {
                attached=0;active=-1;memset(endpoints,0,sizeof(endpoints));
                wr(CTRL,(FS_CTRL&~1u)|256); /* Reattach debounce needs SOF quiet. */
                hcd_event_device_remove(0,false);
            }
        }
    }
    if(resetting || !attached)return;
    if(active>=0) {
        endpoint_t *e=&endpoints[active];uint32_t status=rd(RX_STAT);
        if(!due(now,launched+1))return; /* Avoid stale idle immediately after START. */
        if((status>>31) || !(status&(1u<<28))) {
            if(due(now,launched+20)) {wr(CTRL,FS_CTRL|256);finish(e,XFER_RESULT_FAILED);}
            return;
        }
        wr(IRQ_ACK,IRQ_DONE|IRQ_ERR);
        unsigned count=status&65535,pid=(status>>16)&255;
        uint8_t data[64];
        for(unsigned i=0;i<count && i<64;++i)data[i]=rd(FIFO);
        if(count>64 || (status&((1u<<30)|(1u<<29)))) {retry(e,now,1);return;}
        if(pid==PID_NAK) {retry(e,now,0);return;}
        if(pid==PID_STALL) {finish(e,XFER_RESULT_STALLED);return;}
        if(e->ep&128) {
            if(pid!=PID_DATA0 && pid!=PID_DATA1) {retry(e,now,1);return;}
            if(pid!=(e->toggle?PID_DATA1:PID_DATA0)) {retry(e,now,0);return;}
            if(count>packet_length) {finish(e,XFER_RESULT_FAILED);return;}
            if(count)memcpy(e->buffer+e->done,data,count);
            e->done+=count;e->toggle^=1;e->retries=0;
            if(count<e->mps || e->done==e->length) {finish(e,XFER_RESULT_SUCCESS);return;}
        } else {
            if(pid!=PID_ACK) {retry(e,now,1);return;}
            e->done+=packet_length;e->toggle^=1;e->retries=0;
            if(e->done==e->length) {finish(e,XFER_RESULT_SUCCESS);return;}
        }
        active=-1;e->next=now;
    }
    for(unsigned n=0;n<ENDPOINT_COUNT;++n) {
        unsigned index=(cursor+n)%ENDPOINT_COUNT;endpoint_t *e=&endpoints[index];
        if(!e->used || !e->pending || !due(now,e->next))continue;
        if(e->type!=TUSB_XFER_INTERRUPT && due(now,e->deadline)) {finish(e,XFER_RESULT_FAILED);continue;}
        if(!(rd(RX_STAT)&(1u<<28)))return;
        active=index;cursor=(index+1)%ENDPOINT_COUNT;
        packet_length=e->length-e->done;if(packet_length>e->mps)packet_length=e->mps;
        wr(IRQ_ACK,IRQ_DONE|IRQ_ERR);wr(CTRL,FS_CTRL|256);
        unsigned pid=e->setup?PID_SETUP:((e->ep&128)?PID_IN:PID_OUT);
        if(!(e->ep&128))for(unsigned i=0;i<packet_length;++i)wr(FIFO,e->buffer[e->done+i]);
        wr(TX_LEN,(e->ep&128)?0:packet_length);
        wr(TOKEN,(1u<<31)|(1u<<29)|((e->ep&128)?(1u<<30):0)|
                 ((uint32_t)e->toggle<<28)|(pid<<16)|(e->dev<<9)|((e->ep&15)<<5));
        launched=now;break;
    }
}
unsigned hcd_ultra_validate(unsigned hid_interfaces) {
    unsigned interrupt_eps=0;
    for(unsigned i=0;i<ENDPOINT_COUNT;++i) {
        endpoint_t *e=&endpoints[i];if(!e->used)continue;
        if(!e->mps || e->mps>64 || e->done>e->length || (e->pending && e->length && !e->buffer))return 0;
        if(e->type==TUSB_XFER_INTERRUPT && (e->ep&128))++interrupt_eps;
        for(unsigned j=i+1;j<ENDPOINT_COUNT;++j)if(endpoints[j].used && endpoints[j].dev==e->dev && endpoints[j].ep==e->ep)return 0;
    }
    return initialized && interrupt_eps>=hid_interfaces && active<ENDPOINT_COUNT;
}
uint32_t hcd_ultra_control(void) {return rd(CTRL);}
uint32_t hcd_ultra_status(void) {return rd(STATUS)&7;}
