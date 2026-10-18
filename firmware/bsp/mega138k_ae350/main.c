#include <stdint.h>

/* MUG1029-1.3E tables 3-1 and 7-1: ATCUART offsets, not plain 16550 offsets. */
#define UART2_BASE 0xf0300000u
#define UART_OSCR 0x14u
#define UART_DATA 0x20u
#define UART_IER  0x24u
#define UART_FCR  0x28u
#define UART_LCR  0x2cu
#define UART_LSR  0x34u
#define UART_REG(offset) (*(volatile uint32_t *)(UART2_BASE + (offset)))
#define UART_APB_HZ 100000000u
#define UART_BAUD 115200u

static void putc_uart(char c) {
    while (!(UART_REG(UART_LSR) & 0x20u)) {}
    UART_REG(UART_DATA) = (uint8_t)c;
}
static void puts_uart(const char *s) { while (*s) putc_uart(*s++); }
static void hex32(uint32_t value) {
    for (int i = 28; i >= 0; i -= 4) putc_uart("0123456789abcdef"[(value >> i) & 15u]);
}
static void status(void) {
    uint32_t isa, cycle;
    __asm__ volatile("csrr %0, misa" : "=r"(isa));
    __asm__ volatile("csrr %0, mcycle" : "=r"(cycle));
    puts_uart("misa="); hex32(isa); puts_uart(" mcycle="); hex32(cycle); puts_uart("\r\n");
}
void trap_report(uint32_t cause, uint32_t pc, uint32_t value) {
    puts_uart("TRAP mcause="); hex32(cause); puts_uart(" mepc="); hex32(pc);
    puts_uart(" mtval="); hex32(value); puts_uart("\r\nHALTED; recover with JTAG/reset\r\n");
}
int main(void) {
    const uint32_t divisor = (UART_APB_HZ + 8u * UART_BAUD) / (16u * UART_BAUD);
    UART_REG(UART_LCR) = 0x83u;
    UART_REG(UART_DATA) = divisor & 255u;
    UART_REG(UART_IER) = divisor >> 8;
    UART_REG(UART_LCR) = 3u;
    UART_REG(UART_OSCR) = 16u;
    UART_REG(UART_IER) = 0u;
    UART_REG(UART_FCR) = 7u;
    puts_uart("\r\nmini AE350 P1 ROM build=" MINI_P1_BUILD_ID "; DDR=off core-target=800000000 bus=100000000\r\n");
    puts_uart("s: status; e: diagnostic ebreak\r\n");
    status();
    for (;;) {
        if (!(UART_REG(UART_LSR) & 1u)) continue;
        char c = (char)UART_REG(UART_DATA);
        if (c == 's') status();
        else if (c == 'e') __asm__ volatile("ebreak");
        else putc_uart(c);
    }
}
