#pragma once
#include <stdint.h>
enum {
    GPIO_OK = 0,
    GPIO_ERR_RANGE = 1,
    GPIO_ERR_DIR = 2,
    GPIO_ERR_IDENTITY = 3,
    GPIO_ERR_BUSY = 4,
    GPIO_ERR_UNSUPPORTED = 5
};
int gpio_probe(uint32_t *id);
int gpio_set_dir(unsigned bit, unsigned output);
int gpio_write(unsigned bit, unsigned value);
int gpio_read(unsigned bit, unsigned *value);
int gpio_irq_rising(unsigned bit, unsigned enabled);
uint32_t gpio_irq_status(void);
void gpio_irq_clear(uint32_t bits);
