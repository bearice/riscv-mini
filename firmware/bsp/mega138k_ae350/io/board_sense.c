#include "board_sense.h"
#include "ae350_address_map.h"

static volatile uint32_t *reg(uint32_t offset) {
    return (volatile uint32_t *)(uintptr_t)(AE350_SENSE_BASE + offset);
}

static int identified(void) {
    return *reg(0x00) == AE350_SENSE_ID;
}

int board_fan_set(unsigned percent) {
    if (percent > 100u) return GPIO_ERR_RANGE;
    if (!identified()) return GPIO_ERR_IDENTITY;
    *reg(0x20) = percent;
    return GPIO_OK;
}

int board_fan_get(unsigned *percent) {
    if (!percent) return GPIO_ERR_RANGE;
    if (!identified()) return GPIO_ERR_IDENTITY;
    *percent = *reg(0x20);
    return GPIO_OK;
}

int board_fan_speed(unsigned *hertz, unsigned *rpm) {
    if (!hertz || !rpm) return GPIO_ERR_RANGE;
    if (!identified()) return GPIO_ERR_IDENTITY;
    uint32_t period = *reg(0x2c);
    if (!period) {
        *hertz = 0;
        *rpm = 0;
        return GPIO_OK;
    }
    *hertz = (unsigned)(80000000ull / period);
    *rpm = (unsigned)((80000000ull * 30ull) / period);
    return GPIO_OK;
}

int board_temp_millicelsius(int32_t *millicelsius) {
    if (!millicelsius) return GPIO_ERR_RANGE;
    if (!identified()) return GPIO_ERR_IDENTITY;
    if (*reg(0x10) & 2u) return GPIO_ERR_BUSY;
    *reg(0x0c) = 1u;
    for (uint32_t spin = 0; spin < 2000000u; spin++) {
        uint32_t status = *reg(0x10);
        if (status & 1u) {
            *millicelsius = (int32_t)*reg(0x28);
            return GPIO_OK;
        }
        if (*reg(0x1c) & 1u) return GPIO_ERR_BUSY;
    }
    return GPIO_ERR_BUSY;
}
