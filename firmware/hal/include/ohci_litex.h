#pragma once
#include <generated/mem.h>
#include <generated/soc.h>
#include <hal/hal.h>
#define OHCI_REG ((volatile ohci_registers_t *)USB_OHCI_BASE)
void hcd_int_enable(uint8_t port) {(void)port;hal_irq_enable(USB_HOST_INTERRUPT,1);}
void hcd_int_disable(uint8_t port) {(void)port;hal_irq_enable(USB_HOST_INTERRUPT,0);}
static void ohci_phy_init(uint8_t port) {(void)port;}
