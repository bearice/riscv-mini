#pragma once
#include <stdint.h>
#include "gpio.h"

int board_fan_set(unsigned percent);
int board_fan_get(unsigned *percent);
int board_fan_speed(unsigned *hertz, unsigned *rpm);
int board_temp_millicelsius(int32_t *millicelsius);
