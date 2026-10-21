#include "gpio.h"
#include "ae350_address_map.h"

#define GPIO_IDREV 0x00u
#define GPIO_DATAIN 0x20u
#define GPIO_DATAOUT 0x24u
#define GPIO_DIR 0x28u
#define GPIO_CLEAR 0x2cu
#define GPIO_SET 0x30u
#define GPIO_INTREN 0x50u
#define GPIO_MODE_BASE 0x54u
#define GPIO_INTRSTATUS 0x64u
#define GPIO_INTR_POSITIVE 6u

static int probed;

static volatile uint32_t *greg(uint32_t offset) {
    return (volatile uint32_t *)(uintptr_t)(AE350_GPIO_BASE + offset);
}

static int bit_set(uint32_t mask, unsigned bit) {
    return bit < 32u && ((mask >> bit) & 1u) != 0u;
}

static int published(unsigned bit) {
    return bit_set(AE350_GPIO_BUTTON_MASK | (1u << AE350_GPIO_INJECT_BIT), bit);
}

int gpio_probe(uint32_t *id) {
    uint32_t value = *greg(GPIO_IDREV);
    if (id) *id = value;
    probed = (value >> 12) == AE350_GPIO_ID_PRODUCT;
    return probed ? GPIO_OK : GPIO_ERR_IDENTITY;
}

int gpio_set_dir(unsigned bit, unsigned output) {
    if (!probed) return GPIO_ERR_IDENTITY;
    if (!published(bit)) return GPIO_ERR_RANGE;
    if (output) return GPIO_ERR_DIR;
    uint32_t dir = *greg(GPIO_DIR);
    uint32_t next = output ? (dir | (1u << bit)) : (dir & ~(1u << bit));
    if (next != dir) *greg(GPIO_DIR) = next;
    return GPIO_OK;
}

int gpio_write(unsigned bit, unsigned value) {
    (void)value;
    if (!probed) return GPIO_ERR_IDENTITY;
    if (!published(bit)) return GPIO_ERR_RANGE;
    return GPIO_ERR_DIR;
}

int gpio_read(unsigned bit, unsigned *value) {
    if (!value) return GPIO_ERR_RANGE;
    if (!probed) return GPIO_ERR_IDENTITY;
    if (!published(bit)) return GPIO_ERR_RANGE;
    *value = (*greg(GPIO_DATAIN) >> bit) & 1u;
    return GPIO_OK;
}

int gpio_irq_rising(unsigned bit, unsigned enabled) {
    if (!probed) return GPIO_ERR_IDENTITY;
    if (!bit_set(AE350_GPIO_IRQ_MASK, bit)) return GPIO_ERR_RANGE;
    uint32_t mode_off = GPIO_MODE_BASE + (bit >> 3) * 4u;
    uint32_t shift = (bit & 7u) * 4u;
    uint32_t mode = *greg(mode_off) & ~(7u << shift);
    *greg(mode_off) = mode | (GPIO_INTR_POSITIVE << shift);
    uint32_t enable = *greg(GPIO_INTREN);
    *greg(GPIO_INTREN) = enabled ? (enable | (1u << bit)) : (enable & ~(1u << bit));
    return GPIO_OK;
}

uint32_t gpio_irq_status(void) { return *greg(GPIO_INTRSTATUS); }
void gpio_irq_clear(uint32_t bits) { *greg(GPIO_INTRSTATUS) = bits; }
