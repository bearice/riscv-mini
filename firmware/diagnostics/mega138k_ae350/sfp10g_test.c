/* Tang Mega 138K Pro - SFP+ 10GBASE-R Diagnostic Payload
 *
 * Verifies Gowin SerDes Quad 1 (Lane 0) 10GBASE-R link, Gowin 10G MAC,
 * line-rate packet generator, receiver counters, and APB register interface.
 */
#include <stdint.h>
#include "ae350_address_map.h"

#define UART2_BASE   AE350_UART_BASE
#define UART_DATA    0x20u
#define UART_LSR     0x34u
#define UART_REG(o)  (*(volatile uint32_t *)(UART2_BASE + (o)))

#define SFP_BASE     AE350_SFP_BASE
#define SFP_REG(o)   (*(volatile uint32_t *)(SFP_BASE + (o)))

#define SFP_OFF_ID         0x00u
#define SFP_OFF_CTRL       0x04u
#define SFP_OFF_STATUS     0x08u
#define SFP_OFF_ERR        0x0cu
#define SFP_OFF_RX_FRAMES  0x10u
#define SFP_OFF_TX_FRAMES  0x14u
#define SFP_OFF_RX_BYTES   0x18u
#define SFP_OFF_TX_BYTES   0x1cu
#define SFP_OFF_RX_ERRORS  0x20u

static void putc_uart(char c) {
    while (!(UART_REG(UART_LSR) & 0x20u)) {}
    UART_REG(UART_DATA) = (uint8_t)c;
}

static void puts_uart(const char *s) {
    while (*s) putc_uart(*s++);
}

static void hex32(uint32_t v) {
    for (int i = 28; i >= 0; i -= 4)
        putc_uart("0123456789abcdef"[(v >> i) & 15u]);
}

static void dec32(uint32_t v) {
    char buf[12];
    int i = 0;
    if (v == 0) { putc_uart('0'); return; }
    while (v > 0) {
        buf[i++] = '0' + (v % 10);
        v /= 10;
    }
    while (i > 0) putc_uart(buf[--i]);
}

static inline uint32_t mcycle(void) {
    uint32_t v;
    __asm__ volatile("csrr %0, mcycle" : "=r"(v));
    return v;
}

static void delay_ms(uint32_t ms) {
    /* 800 MHz core clock */
    uint32_t start = mcycle();
    uint32_t cycles = ms * 800000u;
    while ((uint32_t)(mcycle() - start) < cycles) {}
}

static int getc_nonblocking(uint32_t *c) {
    if (UART_REG(UART_LSR) & 1u) {
        *c = UART_REG(UART_DATA) & 0xffu;
        return 1;
    }
    return 0;
}

static void print_status(void) {
    uint32_t id = SFP_REG(SFP_OFF_ID);
    uint32_t ctrl = SFP_REG(SFP_OFF_CTRL);
    uint32_t st = SFP_REG(SFP_OFF_STATUS);
    uint32_t err = SFP_REG(SFP_OFF_ERR);
    uint32_t rx_f = SFP_REG(SFP_OFF_RX_FRAMES);
    uint32_t tx_f = SFP_REG(SFP_OFF_TX_FRAMES);
    uint32_t rx_b = SFP_REG(SFP_OFF_RX_BYTES);
    uint32_t tx_b = SFP_REG(SFP_OFF_TX_BYTES);
    uint32_t rx_e = SFP_REG(SFP_OFF_RX_ERRORS);

    puts_uart("\r\n+------------------------------------------------+\r\n");
    puts_uart("| SFP+ 10GBASE-R Peripheral Status Report        |\r\n");
    puts_uart("+------------------------------------------------+\r\n");
    puts_uart("  ID Register     : 0x"); hex32(id);
    if (id == 0xae35010a) puts_uart(" [OK: AE350 10G SFP]\r\n");
    else puts_uart(" [FAIL: Unexpected ID]\r\n");

    puts_uart("  Control Reg     : 0x"); hex32(ctrl); puts_uart("\r\n");
    puts_uart("  Status Reg      : 0x"); hex32(st); puts_uart("\r\n");
    puts_uart("    - PLL Lock    : "); puts_uart((st & (1 << 0)) ? "LOCKED (156.25 MHz OK)\r\n" : "UNLOCKED [FAIL]\r\n");
    puts_uart("    - Block Lock  : "); puts_uart((st & (1 << 1)) ? "LOCKED (64b/66b sync OK)\r\n" : "UNLOCKED\r\n");
    puts_uart("    - PCS Status  : "); puts_uart((st & (1 << 2)) ? "LINK UP (10GBASE-R OK)\r\n" : "LINK DOWN\r\n");
    puts_uart("    - SFP LOS Pin : "); puts_uart((st & (1 << 3)) ? "SIGNAL LOSS [Check Optical]\r\n" : "NORMAL (Light present)\r\n");
    puts_uart("    - High BER    : "); puts_uart((st & (1 << 4)) ? "HIGH BER [WARN]\r\n" : "NORMAL\r\n");
    puts_uart("    - Local Fault : "); puts_uart((st & (1 << 5)) ? "LOCAL FAULT [WARN]\r\n" : "NORMAL\r\n");
    puts_uart("    - Remote Fault: "); puts_uart((st & (1 << 6)) ? "REMOTE FAULT [WARN]\r\n" : "NORMAL\r\n");
    puts_uart("    - Pkt Gen Busy: "); puts_uart((st & (1 << 7)) ? "BUSY\r\n" : "IDLE\r\n");

    puts_uart("  Error Vector    : 0x"); hex32(err);
    puts_uart(" (BER="); dec32(err & 0x3f);
    puts_uart(", ErrBlocks="); dec32((err >> 8) & 0x7f);
    puts_uart(")\r\n");

    puts_uart("  TX Counters     : Frames="); dec32(tx_f);
    puts_uart(" (0x"); hex32(tx_f);
    puts_uart("), Bytes="); dec32(tx_b); puts_uart("\r\n");

    puts_uart("  RX Counters     : Frames="); dec32(rx_f);
    puts_uart(" (0x"); hex32(rx_f);
    puts_uart("), Bytes="); dec32(rx_b);
    puts_uart(", Errors="); dec32(rx_e); puts_uart("\r\n");
    puts_uart("+------------------------------------------------+\r\n");
}

static void send_packets(uint16_t count) {
    puts_uart(">>> Triggering Packet Generator: sending ");
    dec32(count);
    puts_uart(" frame(s) at 10.3125 Gbps...\r\n");

    /* ctrl register: count in [31:16], arm bit 3 = 1 */
    uint32_t val = ((uint32_t)count << 16) | (1u << 3);
    SFP_REG(SFP_OFF_CTRL) = val;

    /* Wait for transmission to complete */
    delay_ms(10);
}

void main(void) {
    puts_uart("\r\n==================================================\r\n");
    puts_uart("  Tang Mega 138K Pro - SFP+ 10G Diagnostic Tool   \r\n");
    puts_uart("==================================================\r\n");

    print_status();

    uint32_t st = SFP_REG(SFP_OFF_STATUS);
    if ((st & 0x07) == 0x07) {
        puts_uart("[PASS] SerDes + 10G PCS + MAC are fully synchronized and LINK IS UP!\r\n");
    } else {
        puts_uart("[INFO] Link synchronization in progress or optical transceiver unplugged.\r\n");
    }

    /* Send first batch of 10 test frames */
    send_packets(10);
    print_status();

    puts_uart("\r\nEntering interactive monitor mode...\r\n");
    puts_uart("Commands:\r\n");
    puts_uart("  't': send 10 test packets to host\r\n");
    puts_uart("  'T': send 1000 test packets to host\r\n");
    puts_uart("  'c': clear statistics counters\r\n");
    puts_uart("  's': display status report\r\n");
    puts_uart("  'q': quit\r\n\r\n");

    uint32_t last_rx = SFP_REG(SFP_OFF_RX_FRAMES);
    uint32_t last_tx = SFP_REG(SFP_OFF_TX_FRAMES);

    for (;;) {
        uint32_t c;
        if (getc_nonblocking(&c)) {
            if (c == 't') {
                send_packets(10);
                print_status();
            } else if (c == 'T') {
                send_packets(1000);
                print_status();
            } else if (c == 'c') {
                puts_uart(">>> Clearing statistics counters...\r\n");
                SFP_REG(SFP_OFF_CTRL) = (1u << 4);
                delay_ms(5);
                print_status();
            } else if (c == 's') {
                print_status();
            } else if (c == 'q') {
                puts_uart("Exiting diagnostic tool. Halting.\r\n");
                break;
            }
        }

        uint32_t cur_rx = SFP_REG(SFP_OFF_RX_FRAMES);
        uint32_t cur_tx = SFP_REG(SFP_OFF_TX_FRAMES);
        if (cur_rx != last_rx || cur_tx != last_tx) {
            puts_uart("[UPDATE] TX_FRAMES="); dec32(cur_tx);
            puts_uart(" RX_FRAMES="); dec32(cur_rx);
            puts_uart(" RX_BYTES="); dec32(SFP_REG(SFP_OFF_RX_BYTES));
            puts_uart(" RX_ERR="); dec32(SFP_REG(SFP_OFF_RX_ERRORS));
            puts_uart("\r\n");
            last_rx = cur_rx;
            last_tx = cur_tx;
        }

        delay_ms(50);
    }

    puts_uart("DIAG DONE\r\n");
    for (;;) { __asm__ volatile("wfi"); }
}
