#pragma once
#include <stdint.h>
#include "ae350_address_map.h"
#define USB_PIO_BASE AE350_USB_BASE
#define USB_HOST_INTERRUPT 0
uint32_t hal_time_ms(void);
static inline void hal_irq_enable(unsigned irq,unsigned enable) {(void)irq;(void)enable;}
