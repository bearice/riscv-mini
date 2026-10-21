#include "board_io.h"
#include "ae350_address_map.h"

static volatile uint32_t *page(uint32_t base, uint32_t offset) {
    return (volatile uint32_t *)(uintptr_t)(base + offset);
}

int board_led_count(void) { return (int)AE350_GPIO_LED_COUNT; }
int board_button_count(void) { return (int)AE350_GPIO_BUTTON_COUNT; }
int board_switch_count(void) { return 0; }

int board_led_set(unsigned index, unsigned on) {
    if (index >= AE350_GPIO_LED_COUNT) return GPIO_ERR_RANGE;
    if (*page(AE350_IO_BASE, 0x00) != AE350_IO_ID) return GPIO_ERR_IDENTITY;
    uint32_t mask = 1u << index;
    uint32_t cur = *page(AE350_IO_BASE, 0x28);
    *page(AE350_IO_BASE, 0x28) = on ? (cur | mask) : (cur & ~mask);
    return GPIO_OK;
}

int board_led_get(unsigned index, unsigned *on) {
    if (!on || index >= AE350_GPIO_LED_COUNT) return GPIO_ERR_RANGE;
    if (*page(AE350_IO_BASE, 0x00) != AE350_IO_ID) return GPIO_ERR_IDENTITY;
    *on = (*page(AE350_IO_BASE, 0x28) >> index) & 1u;
    return GPIO_OK;
}

int board_button_get(unsigned index, unsigned *pressed) {
    if (!pressed) return GPIO_ERR_RANGE;
    if (index >= AE350_GPIO_BUTTON_COUNT) return GPIO_ERR_RANGE;
    return gpio_read(AE350_GPIO_BUTTON_BIT + index, pressed);
}

int board_events_take(unsigned *pressed, unsigned *released) {
    uint32_t status = *page(AE350_IO_BASE, 0x14);
    if (pressed) *pressed = status & 0xfu;
    if (released) *released = (status >> 4) & 0xfu;
    *page(AE350_IO_BASE, 0x14) = status & 0xffu;
    return GPIO_OK;
}

int board_ws2812_set(uint8_t red, uint8_t green, uint8_t blue) {
    if (*page(AE350_WS2812_BASE, 0x00) != AE350_WS2812_ID) return GPIO_ERR_IDENTITY;
    if (*page(AE350_WS2812_BASE, 0x10) & 1u) return GPIO_ERR_BUSY;
    *page(AE350_WS2812_BASE, 0x20) = ((uint32_t)red << 16) | ((uint32_t)green << 8) | blue;
    *page(AE350_WS2812_BASE, 0x0c) = 1u;
    if (*page(AE350_WS2812_BASE, 0x1c) == AE350_ERR_BUSY) return GPIO_ERR_BUSY;
    return GPIO_OK;
}

int board_ws2812_busy(unsigned *busy) {
    if (!busy) return GPIO_ERR_RANGE;
    if (*page(AE350_WS2812_BASE, 0x00) != AE350_WS2812_ID) return GPIO_ERR_IDENTITY;
    *busy = *page(AE350_WS2812_BASE, 0x10) & 1u;
    return GPIO_OK;
}

uint32_t board_ws2812_completed(void) { return *page(AE350_WS2812_BASE, 0x24); }
uint32_t board_diag_edges(void) { return *page(AE350_IO_BASE, 0x20); }

void board_diag_pulse(void) {
    uint32_t control = *page(AE350_IO_BASE, 0x0c);
    *page(AE350_IO_BASE, 0x0c) = (control & 2u) | 4u;
}

void board_irq_enable(unsigned enabled) {
    *page(AE350_IO_BASE, 0x18) = enabled ? 1u : 0u;
}

void board_diag_clear(void) { *page(AE350_IO_BASE, 0x14) = 0x100u; }
uint32_t board_event_status(void) { return *page(AE350_IO_BASE, 0x14); }
