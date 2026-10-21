/* Minimal independent AE350 application; inspect the result through JTAG. */
#include <stdint.h>
#include "ae350_address_map.h"
#ifndef AE350_DEMO_VALUE
#define AE350_DEMO_VALUE 0x12345678u
#endif
void main(void) {
    volatile uint32_t *result = (volatile uint32_t *)AE350_RESULTS_BASE;
    result[0] = 0xa3500001u;
    result[1] = AE350_DEMO_VALUE;
    for (;;) __asm__ volatile("wfi");
}
