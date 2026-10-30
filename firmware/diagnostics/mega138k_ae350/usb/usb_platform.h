#pragma once
#include <stdint.h>
#define USB_PIO_BASE 0xf8060000u
#define USB_HOST_INTERRUPT 0
uint32_t hal_time_ms(void);
static inline void hal_irq_enable(unsigned irq,unsigned enable) {(void)irq;(void)enable;}
