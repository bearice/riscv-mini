#pragma once
#include <stdint.h>
#include "gpio.h"
int board_led_count(void);
int board_button_count(void);
int board_switch_count(void);
int board_led_set(unsigned index, unsigned on);
int board_led_get(unsigned index, unsigned *on);
int board_button_get(unsigned index, unsigned *pressed);
int board_events_take(unsigned *pressed, unsigned *released);
int board_ws2812_set(uint8_t red, uint8_t green, uint8_t blue);
int board_ws2812_busy(unsigned *busy);
uint32_t board_ws2812_completed(void);
uint32_t board_diag_edges(void);
void board_diag_pulse(void);
void board_irq_enable(unsigned enabled);
void board_diag_clear(void);
uint32_t board_event_status(void);
