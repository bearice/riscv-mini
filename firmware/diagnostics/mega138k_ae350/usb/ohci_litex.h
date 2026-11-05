#pragma once
#include "ae350_address_map.h"
#ifndef USB_ORDERED_MMIO
#define USB_ORDERED_MMIO 0
#endif
#if USB_ORDERED_MMIO
static inline volatile ohci_registers_t *ohci_ordered_registers(void) {
    /* ROM leaves L1D disabled. A DDR read drains the separate AHB posted
     * write buffer before publishing descriptor changes through MMIO.
     * This diagnostic switch does not supply cache maintenance for Linux. */
    uint32_t readback;
    __asm__ volatile("fence iorw, iorw\n\tlw %0, 0(%1)\n\tfence iorw, iorw"
                     : "=&r"(readback) : "r"(AE350_DDR_BASE+32u) : "memory");
    return (volatile ohci_registers_t *)AE350_USB_BASE;
}
#define OHCI_REG ohci_ordered_registers()
#else
#define OHCI_REG ((volatile ohci_registers_t *)AE350_USB_BASE)
#endif
/* Qualification polls hcd_int_handler; PLIC remains masked here. */
void hcd_int_enable(uint8_t port) {(void)port;}
void hcd_int_disable(uint8_t port) {(void)port;}
static void ohci_phy_init(uint8_t port) {(void)port;}
