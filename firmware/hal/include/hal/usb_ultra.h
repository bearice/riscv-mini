#pragma once
#include <stdint.h>
void hcd_ultra_poll(void);
unsigned hcd_ultra_validate(unsigned hid_interfaces);
uint32_t hcd_ultra_control(void);
uint32_t hcd_ultra_status(void);
