#include <stdint.h>
#include "ae350_address_map.h"
__attribute__((weak, noreturn))
void trap_report(uint32_t cause, uint32_t pc, uint32_t value, uint32_t ra, uint32_t sp) {
    volatile uint32_t *result = (volatile uint32_t *)AE350_RESULTS_BASE;
    result[AE350_TRAP_RECORD] = 0x80000000u | cause;
    result[0xa1] = pc; result[0xa2] = value; result[0xa3] = ra; result[0xa4] = sp;
    for (;;) __asm__ volatile("wfi");
}

__attribute__((weak)) void ecall_dispatch(uint32_t *frame) {frame[8]=(uint32_t)-1;}
