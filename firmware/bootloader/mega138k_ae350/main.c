/* AE350 platform ROM bootloader: autonomous POST, then UART payload loader.
 *
 * Boot flow (no JTAG, no host magic):
 *   reset -> _start -> main
 *     -> short POST: DLM scratch pattern, DDR calibration status, DDR word check, ROM CRC
 *     -> results mirrored in DLM AE350_RESULTS_BASE
 *     -> UART identity banner and loader menu (20K framing, AE350 ABI)
 *
 * ROM-private stack/data use DLM. CRC runs in explicitly ROM-loaded ILM.
 * Diagnostic results occupy a reserved DLM region; no fabric work SRAM.
 */
#include <stdint.h>
#include "ae350_address_map.h"

#define UART2_BASE   0xf0300000u
#define UART_OSCR    0x14u
#define UART_DATA    0x20u
#define UART_IER     0x24u
#define UART_FCR     0x28u
#define UART_LCR     0x2cu
#define UART_LSR     0x34u
#define UART_REG(o)  (*(volatile uint32_t *)(UART2_BASE + (o)))
#define UART_APB_HZ  AE350_FABRIC_HZ
#define UART_BAUD    115200u

#define RES          ((volatile uint32_t *)AE350_RESULTS_BASE)

#define ROM_STATUS   (*(volatile uint32_t *)AE350_STATUS_ADDR)
#define ROM_CYCLES   (*(volatile uint32_t *)AE350_CALIB_CYCLES_ADDR)
#define DDR_BASE     0x00000000u
#define DDR_PROBE    0x00100000u


static void putc(char c) {
    while (!(UART_REG(UART_LSR) & 0x20u)) { }
    UART_REG(UART_DATA) = (uint8_t)c;
}
static void puts(const char *s) { while (*s) putc(*s++); }
static void hex32(uint32_t v) {
    for (int i = 28; i >= 0; i -= 4) putc("0123456789abcdef"[(v >> i) & 15u]);
}
static void uart_init(void) {
    uint32_t d = (UART_APB_HZ + 8u * UART_BAUD) / (16u * UART_BAUD);
    UART_REG(UART_LCR) = 0x83u;
    UART_REG(UART_DATA) = d & 255u;
    UART_REG(UART_IER) = d >> 8;
    UART_REG(UART_LCR) = 0x03u;
    UART_REG(UART_OSCR) = 16u;
    UART_REG(UART_IER) = 0u;
    UART_REG(UART_FCR) = 7u;
}

void trap_report(uint32_t cause, uint32_t pc, uint32_t value, uint32_t ra, uint32_t sp) {
    (void)ra; (void)sp;
    RES[0xF0] = 0x80000000u | cause;
    RES[0xF1] = pc;
    RES[0xF2] = value;
    puts("\r\nERR TRAP mcause="); hex32(cause);
    puts(" mepc="); hex32(pc);
    puts(" mtval="); hex32(value);
    puts("\r\nHALTED; recover with JTAG/reset\r\n");
    for (;;) { }
}

/* ------------------------------------------------------------------ POST --- */
static uint32_t post_dlm(void) {
    /* The reserved params block: the stack lives above it (linker-asserted). */
    volatile uint32_t *p = (volatile uint32_t *)AE350_LOCAL_PARAMS;
    for (uint32_t i = 0; i < 64u; i++) p[i] = 0xa5a50000u | i;
    for (uint32_t i = 0; i < 64u; i++) if (p[i] != (0xa5a50000u | i)) return 1;
    for (uint32_t i = 0; i < 64u; i++) p[i] = ~(0x5a5a0000u | i);
    for (uint32_t i = 0; i < 64u; i++) if (p[i] != ~(0x5a5a0000u | i)) return 2;
    return 0;
}
static uint32_t post_rom(void) {
    const uint32_t *w = (const uint32_t *)0x80000000u;
    uint32_t sum = 0;
    for (uint32_t i = 0; i < 64u; i++) sum = (sum << 1) | (sum >> 31), sum += w[i];
    return sum;
}
static uint32_t post_ddr(void) {
    volatile uint32_t *p = (volatile uint32_t *)DDR_PROBE;
    p[0] = 0xc0de0001u;
    p[8] = 0xc0de0002u;
    __asm__ volatile("fence" ::: "memory");
    if (p[0] != 0xc0de0001u) return 1;
    if (p[8] != 0xc0de0002u) return 2;
    return 0;
}

/* ------------------------------------------------------------- UART loader ---
 *
 * Reuses the shared 20K framing (scripts/primer20k_vexriscv/boot_image.py):
 *   header: 12 little-endian u32 = magic, version, header size, abi,
 *           payload length, load, entry, crc32(payload), 0, 0, 0,
 *           crc32(header[0:44])
 *   packet: u32 seq, u16 length, data, u32 crc32(seq|length|data)
 *   ack   : 'K' followed by the u32 sequence number
 */
#define IMG_MAGIC   0x354d5652u
#define IMG_VERSION 1u
#define IMG_HDR     48u
#define CHUNK       128u
#define AE350_LOAD  AE350_DDR_CODE
#define AE350_MAX   AE350_DDR_CODE_MAX

static uint8_t  img[IMG_HDR];
static uint32_t payload_left;
static uint32_t load_addr;
static uint32_t seq_expected;
static uint32_t run_crc;

__attribute__((section(".ilm.text"), noinline))
static uint32_t crc32_step(uint32_t crc, uint32_t byte) {
    crc ^= byte & 0xffu;
    for (int i = 0; i < 8; i++) crc = (crc >> 1) ^ (0xedb88320u & (~(crc & 1u) + 1u));
    return crc;
}
static int getc_timeout(uint32_t *out, uint32_t spins) {
    while (spins--) if (UART_REG(UART_LSR) & 1u) { *out = UART_REG(UART_DATA) & 0xffu; return 1; }
    return 0;
}
static void send_ack(uint32_t seq) {
    putc('K');
    for (int i = 0; i < 4; i++) putc((seq >> (8 * i)) & 0xffu);
}
/* ~10 s at the configured core clock: long enough for a 128-byte packet at
 * 115200 (11 ms), short enough that an aborted upload recovers quickly. */
static int recv_byte(uint32_t *b) { return getc_timeout(b, 40000000u); }
static int recv_word(uint32_t *w) {
    uint32_t b;
    *w = 0;
    for (int i = 0; i < 4; i++) {
        if (!recv_byte(&b)) return 0;
        *w |= b << (8 * i);
    }
    return 1;
}

static int ddr_usable;

static void loader(void) {
    /* A started upload runs to completion before accepting another command. */
    for (;;) {
        uint32_t c = 0;
        if (!getc_timeout(&c, 5000u)) return;   /* ~tens of microseconds per slice */
        if (c != 'u') continue;
        if (!ddr_usable) { puts("ERR DDR INIT\r\n"); goto retry; }
        puts("READY HEADER\r\n");
        for (uint32_t i = 0; i < IMG_HDR; i++) {
            uint32_t b;
            if (!recv_byte(&b)) { puts("ERR HEADER TIMEOUT\r\n"); goto retry; }
            img[i] = (uint8_t)b;
        }
        {
            const uint32_t *h = (const uint32_t *)img;
            uint32_t hc = 0xffffffffu;
            for (uint32_t i = 0; i < IMG_HDR - 4u; i++) hc = crc32_step(hc, img[i]);
            hc ^= 0xffffffffu;
            if (h[0] != IMG_MAGIC || h[1] != IMG_VERSION || h[2] != IMG_HDR ||
                h[8] || h[9] || h[10] || hc != h[11]) { puts("ERR HEADER\r\n"); goto retry; }
            if (h[3] != AE350_IMAGE_ABI) { puts("ERR ABI\r\n"); goto retry; }
            if (h[4] < 4u || h[4] > AE350_MAX || h[5] != AE350_LOAD || h[6] != AE350_LOAD) {
                puts("ERR RANGE\r\n"); goto retry;
            }
            payload_left = h[4];
            load_addr = h[5];
        }
        puts("READY DATA\r\n");
        seq_expected = 0;
        run_crc = 0xffffffffu;
        {
            uint32_t done = 0, err = 0;
            while (done < payload_left) {
                uint32_t seq, len16 = 0, n, crc_hdr = 0xffffffffu;
                if (!recv_word(&seq)) { err = 1; break; }
                for (int i = 0; i < 2; i++) {      /* the length field is 16 bit */
                    uint32_t b;
                    if (!recv_byte(&b)) { err = 1; break; }
                    len16 |= b << (8 * i);
                }
                if (err) break;
                n = len16;
                if (seq != seq_expected || n == 0 || n > CHUNK || done + n > payload_left) { err = 1; break; }
                for (int i = 0; i < 4; i++) crc_hdr = crc32_step(crc_hdr, (seq >> (8 * i)) & 0xffu);
                for (int i = 0; i < 2; i++) crc_hdr = crc32_step(crc_hdr, (len16 >> (8 * i)) & 0xffu);
                for (uint32_t i = 0; i < n; i++) {
                    uint32_t b;
                    if (!recv_byte(&b)) { err = 1; break; }
                    *(volatile uint8_t *)(load_addr + done + i) = (uint8_t)b;
                    run_crc = crc32_step(run_crc, b);
                    crc_hdr = crc32_step(crc_hdr, b);
                }
                if (err) break;
                {
                    uint32_t want;
                    if (!recv_word(&want)) { err = 1; break; }
                    crc_hdr ^= 0xffffffffu;
                    if (want != crc_hdr) { err = 1; break; }
                }
                send_ack(seq);
                done += n;
                seq_expected++;
            }
            if (err || done != payload_left) { puts("ERR UPLOAD\r\n"); goto retry; }
        }
        run_crc ^= 0xffffffffu;
        {
            const uint32_t *h = (const uint32_t *)img;
            if (run_crc != h[7]) { puts("ERR CRC\r\n"); goto retry; }
        }
        puts("SYSTEM READY\r\n> ");
        __asm__ volatile("fence.i" ::: "memory");
        void (*entry)(void) = (void (*)(void))load_addr;
        entry();
    retry:
        puts("BL> ");
    }
}

void main(void) {
    uint32_t errs = 0;
    uart_init();
    __asm__ volatile("csrr %0, 0x7c0" : "=r"(RES[8]));
    __asm__ volatile("csrr %0, 0x7c1" : "=r"(RES[9]));
    __asm__ volatile("csrr %0, 0xfc0" : "=r"(RES[10]));
    __asm__ volatile("csrr %0, 0xfc1" : "=r"(RES[11]));
    RES[0] = 0xd0b00001u;                 /* POST started */
    RES[1] = post_dlm();                 /* 0 = pass */
    errs += RES[1] ? 1u : 0u;
    RES[2] = post_rom();          /* digest reported, not compared */
    {
        uint32_t st = ROM_STATUS;
        RES[3] = st;
        RES[4] = ROM_CYCLES;
        if (!(st & 2u)) errs++;           /* DDR calibration complete */
        if (st & 4u) errs++;              /* calibration watchdog */
        ddr_usable = ((st & 2u) && !(st & 4u)) ? 1 : 0;
    }
    if (ddr_usable) {
        RES[5] = post_ddr();
        errs += RES[5] ? 1u : 0u;
    } else {
        RES[5] = 0xffffffffu;             /* DDR not usable: skip the word check */
    }
    RES[6] = errs;
    RES[7] = 0xd0b00002u;                 /* POST done */

    puts("\r\nriscv-mini AE350 ROM " MINI_AE350_BOOT_BUILD_ID "\r\n");
    puts("POST ");
    hex32(errs ? 0xbad00000u : 0x600d0000u); puts("\r\n");
    if (RES[3] & 2u) puts("DDR INIT OK\r\n"); else puts("ERR DDR INIT; DLM/JTAG only\r\n");
    puts("BL> ");

    for (;;) loader();
}
