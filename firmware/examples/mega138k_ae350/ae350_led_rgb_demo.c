/* Board I/O demo: KEY0/KEY1 select a user LED; KEY2/KEY3 select RGB.
 * Die temperature is reported independently. Fan control is outside acceptance
 * and is never written by this demo; configured-fabric default stays full on.
 */
#include <stdint.h>
#include "ae350_address_map.h"
#include "board_io.h"
#include "board_sense.h"

#define UART2 0xf0300000u

static const uint8_t palette[][3] = {
    {48, 0, 0},
    {0, 48, 0},
    {0, 0, 48},
    {48, 48, 0},
    {0, 48, 48},
    {48, 0, 48},
    {48, 48, 48},
    {0, 0, 0},
};

static void putc(char c) {
    volatile uint32_t *lsr = (volatile uint32_t *)(UART2 + 0x34u);
    volatile uint32_t *data = (volatile uint32_t *)(UART2 + 0x20u);
    while ((*lsr & 0x20u) == 0u) { }
    *data = (uint8_t)c;
}

static void puts(const char *text) {
    while (*text) putc(*text++);
}

static void put_i32(int32_t value) {
    char buf[12];
    unsigned n = 0;
    uint32_t mag;
    if (value < 0) {
        putc('-');
        mag = (uint32_t)(-value);
    } else {
        mag = (uint32_t)value;
    }
    do {
        buf[n++] = (char)('0' + (mag % 10u));
        mag /= 10u;
    } while (mag);
    while (n) putc(buf[--n]);
}

static void show_led(unsigned index) {
    for (unsigned led = 0; led < (unsigned)board_led_count(); led++)
        board_led_set(led, led == index);
}

static void show_rgb(unsigned index) {
    unsigned busy = 1u;
    for (uint32_t spin = 0; spin < 200000u && busy; spin++) {
        if (board_ws2812_busy(&busy)) return;
    }
    board_ws2812_set(palette[index][0], palette[index][1], palette[index][2]);
}

void main(void) {
    unsigned rgb = 0;
    unsigned colors = sizeof palette / sizeof palette[0];
    unsigned led = 0;
    show_rgb(rgb);
    show_led(led);
    puts("KEY0 next LED  KEY1 prev LED  KEY2 next RGB  KEY3 prev RGB\r\n");
    uint32_t temp_at = 0;
    unsigned temp_wait = 0;
    for (;;) {
        uint32_t now;
        __asm__ volatile("csrr %0, mcycle" : "=r"(now));
        if (!temp_wait && (int32_t)(now - temp_at) >= 0) {
            *(volatile uint32_t *)(uintptr_t)(AE350_SENSE_BASE + 0x0cu) = 1u;
            temp_wait = 1u;
        }
        if (temp_wait) {
            uint32_t status = *(volatile uint32_t *)(uintptr_t)(AE350_SENSE_BASE + 0x10u);
            uint32_t error = *(volatile uint32_t *)(uintptr_t)(AE350_SENSE_BASE + 0x1cu);
            *(volatile uint32_t *)(uintptr_t)(AE350_RESULTS_BASE + 0u) = status;
            *(volatile uint32_t *)(uintptr_t)(AE350_RESULTS_BASE + 4u) = error;
            if (status & 1u) {
                int32_t mdeg = (int32_t)(*(volatile uint32_t *)(uintptr_t)(AE350_SENSE_BASE + 0x28u));
                *(volatile uint32_t *)(uintptr_t)(AE350_RESULTS_BASE + 8u) = (uint32_t)mdeg;
                unsigned hertz = 0;
                unsigned rpm = 0;
                board_fan_speed(&hertz, &rpm);
                puts("TEMP ");
                put_i32(mdeg);
                puts(" mC TACH ");
                put_i32((int32_t)hertz);
                puts(" Hz ");
                put_i32((int32_t)rpm);
                puts(" rpm\r\n");
                temp_wait = 0;
                temp_at = now + 1600000000u;
            } else if ((status & 2u) == 0u && error) {
                puts("TEMP ERR\r\n");
                temp_wait = 0;
                temp_at = now + 1600000000u;
            }
        }
        unsigned pressed = 0;
        board_events_take(&pressed, 0);
        if (pressed & 1u) {
            led = (led + 1u) % (unsigned)board_led_count();
            show_led(led);
        }
        if (pressed & 2u) {
            led = (led + (unsigned)board_led_count() - 1u) % (unsigned)board_led_count();
            show_led(led);
        }
        if (pressed & 4u) {
            rgb = (rgb + 1u) % colors;
            show_rgb(rgb);
            puts("RGB+\r\n");
        }
        if (pressed & 8u) {
            rgb = (rgb + colors - 1u) % colors;
            show_rgb(rgb);
            puts("RGB-\r\n");
        }
    }
}
