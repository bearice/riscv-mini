/* Exercise the real TinyUSB enumeration callback and Ultra HCD rejection. */
#include <assert.h>
#include <stdio.h>
#include <hal/hal.h>
#include <hal/usb_ultra.h>
#include "../../firmware/vendor/tinyusb/src/host/usbh.c"

static uint32_t ticks;
uint32_t hal_time_ms(void) { return ++ticks; }
uint32_t tusb_time_millis_api(void) { return hal_time_ms(); }
void tusb_time_delay_ms_api(uint32_t ms) { ticks += ms; }
hal_result_t hal_irq_enable(unsigned source, unsigned enabled) {
    (void)source; (void)enabled; return HAL_OK;
}
uint32_t rd(unsigned offset) { (void)offset; return 0; }
void wr(unsigned offset, uint32_t value) { (void)offset; (void)value; }
void tuh_hid_report_received_cb(uint8_t device, uint8_t itf, const uint8_t *data, uint16_t len) {
    (void)device; (void)itf; (void)data; (void)len;
}

int main(void) {
    tusb_rhport_init_t init = {.role=TUSB_ROLE_HOST, .speed=TUSB_SPEED_FULL};
    assert(hcd_init(0, &init));
    _usbh_data.dev0_bus.rhport = 0;
    _usbh_data.dev0_bus.hub_addr = CFG_TUH_DEVICE_MAX+1;
    _usbh_data.dev0_bus.hub_port = 2;
    _usbh_data.dev0_bus.speed = TUSB_SPEED_LOW;
    _usbh_data.enumerating_daddr = 0;
    tuh_xfer_t xfer = {.daddr=0, .result=XFER_RESULT_SUCCESS,
                      .user_data=ENUM_ADDR0_DEVICE_DESC};
    process_enumeration(&xfer);
    assert(_usbh_data.enumerating_daddr == TUSB_INDEX_INVALID_8);
    /* Rejecting one unsupported device must not consume control EP slots. */
    _usbh_data.dev0_bus.speed = TUSB_SPEED_FULL;
    assert(usbh_edpt_control_open(0, 8));
    puts("PASS TinyUSB unsupported-speed enumeration releases the queue");
    return 0;
}
