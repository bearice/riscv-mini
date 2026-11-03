/* Tang Mega 138K Pro - Dual SFP (10G / 1G) Ethernet DMA Diagnostic Payload
 *
 * Verifies simultaneous dual SFP ports:
 * - SFP0: 10GBASE-R optical Ethernet via Gowin SerDes Lane 0 + 10G MAC IP (156.25 MHz)
 * - SFP1: 1G BASE-X / SGMII Ethernet via Gowin SerDes Lane 1 + 1G MAC IP (125 MHz)
 * - 32-bit EthernetDMA with DDR3 autonomous descriptor rings
 * - Real-time switching between SFP0 (10G), SFP1 (1G), and RGMII
 * - Full ARP, ICMP Ping, and UDP Echo support on active port
 */
#include <stdint.h>
#include <string.h>
#include "ae350_address_map.h"
#include "ethernet_dma.h"

#define UART2_BASE   AE350_UART_BASE
#define UART_DATA    0x20u
#define UART_LSR     0x34u
#define UART_REG(o)  (*(volatile uint32_t *)(UART2_BASE + (o)))

#define SFP_BASE     AE350_SFP_BASE
#define SFP_REG(o)   (*(volatile uint32_t *)(SFP_BASE + (o)))

/* SFP0 (10G) Registers */
#define SFP0_OFF_ID         0x00u
#define SFP0_OFF_CTRL       0x04u
#define SFP0_OFF_STATUS     0x08u
#define SFP0_OFF_ERR        0x0cu
#define SFP0_OFF_RX_FRAMES  0x10u
#define SFP0_OFF_TX_FRAMES  0x14u
#define SFP0_OFF_RX_BYTES   0x18u
#define SFP0_OFF_TX_BYTES   0x1cu
#define SFP0_OFF_RX_ERRORS  0x20u

/* SFP1 (1G) Registers */
#define SFP1_OFF_ID         0x40u
#define SFP1_OFF_CTRL       0x44u
#define SFP1_OFF_STATUS     0x48u
#define SFP1_OFF_LP_ABILITY 0x4cu
#define SFP1_OFF_RX_FRAMES  0x50u
#define SFP1_OFF_TX_FRAMES  0x54u
#define SFP1_OFF_RX_BYTES   0x58u
#define SFP1_OFF_TX_BYTES   0x5cu
#define SFP1_OFF_RX_ERRORS  0x60u

static const uint8_t BOARD_MAC[6] = {0x02, 0x35, 0x00, 0x00, 0x00, 0x01};
static const uint8_t BOARD_IP[4]  = {192, 0, 2, 2};

static void putc_uart(char c) {
    while (!(UART_REG(UART_LSR) & 0x20u)) {}
    UART_REG(UART_DATA) = (uint8_t)c;
}

static void puts_uart(const char *s) {
    while (*s) putc_uart(*s++);
}

static void hex8(uint8_t v) {
    putc_uart("0123456789abcdef"[(v >> 4) & 15u]);
    putc_uart("0123456789abcdef"[v & 15u]);
}

static void hex16(uint16_t v) {
    for (int i = 12; i >= 0; i -= 4)
        putc_uart("0123456789abcdef"[(v >> i) & 15u]);
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

static int getc_nonblocking(uint32_t *c) {
    if (UART_REG(UART_LSR) & 1u) {
        *c = UART_REG(UART_DATA) & 0xffu;
        return 1;
    }
    return 0;
}

static void print_mac(const uint8_t *m) {
    for (int i = 0; i < 6; i++) {
        hex8(m[i]);
        if (i < 5) putc_uart(':');
    }
}

static void print_ip(const uint8_t *ip) {
    for (int i = 0; i < 4; i++) {
        dec32(ip[i]);
        if (i < 3) putc_uart('.');
    }
}

static uint16_t ip_checksum(const void *buf, int len) {
    const uint16_t *p = (const uint16_t *)buf;
    uint32_t sum = 0;
    while (len > 1) {
        sum += *p++;
        len -= 2;
    }
    if (len == 1) sum += *(const uint8_t *)p;
    while (sum >> 16) sum = (sum & 0xffff) + (sum >> 16);
    return ~sum;
}

static int active_port = 1; /* 0: RGMII, 1: SFP0 (10G), 2: SFP1 (1G) */

static void select_dma_port(int port) {
    active_port = port;
    if (port == 1) {
        /* SFP0 10G active: set SFP0 dma_en=1, SFP1 dma_en=0 */
        uint32_t c0 = SFP_REG(SFP0_OFF_CTRL);
        SFP_REG(SFP0_OFF_CTRL) = c0 | (1 << 5);
        uint32_t c1 = SFP_REG(SFP1_OFF_CTRL);
        SFP_REG(SFP1_OFF_CTRL) = c1 & ~(1 << 5);
        puts_uart("[PORT SWITCH] DMA routed to SFP0 (10GBASE-R)\r\n");
    } else if (port == 2) {
        /* SFP1 1G active: set SFP1 dma_en=1 */
        uint32_t c1 = SFP_REG(SFP1_OFF_CTRL);
        SFP_REG(SFP1_OFF_CTRL) = c1 | (1 << 5);
        puts_uart("[PORT SWITCH] DMA routed to SFP1 (1G BASE-X / SGMII)\r\n");
    } else {
        /* RGMII active: clear both dma_en */
        uint32_t c0 = SFP_REG(SFP0_OFF_CTRL);
        SFP_REG(SFP0_OFF_CTRL) = c0 & ~(1 << 5);
        uint32_t c1 = SFP_REG(SFP1_OFF_CTRL);
        SFP_REG(SFP1_OFF_CTRL) = c1 & ~(1 << 5);
        puts_uart("[PORT SWITCH] DMA routed to RGMII (1G Onboard PHY)\r\n");
    }
}

static void toggle_sfp1_mode(void) {
    uint32_t c1 = SFP_REG(SFP1_OFF_CTRL);
    int new_mode = !(c1 & (1 << 6));
    if (new_mode) {
        SFP_REG(SFP1_OFF_CTRL) = c1 | (1 << 6);
        puts_uart("[SFP1 MODE] Switched to SGMII copper mode (for SFP-GE-T RJ45)\r\n");
    } else {
        SFP_REG(SFP1_OFF_CTRL) = c1 & ~(1 << 6);
        puts_uart("[SFP1 MODE] Switched to 1000BASE-X optical mode\r\n");
    }
}

static void print_status(void) {
    uint32_t sfp0_id     = SFP_REG(SFP0_OFF_ID);
    uint32_t sfp0_ctrl   = SFP_REG(SFP0_OFF_CTRL);
    uint32_t sfp0_st     = SFP_REG(SFP0_OFF_STATUS);
    uint32_t sfp0_rx_f   = SFP_REG(SFP0_OFF_RX_FRAMES);
    uint32_t sfp0_tx_f   = SFP_REG(SFP0_OFF_TX_FRAMES);
    uint32_t sfp0_rx_b   = SFP_REG(SFP0_OFF_RX_BYTES);
    uint32_t sfp0_tx_b   = SFP_REG(SFP0_OFF_TX_BYTES);
    uint32_t sfp0_rx_e   = SFP_REG(SFP0_OFF_RX_ERRORS);

    uint32_t sfp1_id     = SFP_REG(SFP1_OFF_ID);
    uint32_t sfp1_ctrl   = SFP_REG(SFP1_OFF_CTRL);
    uint32_t sfp1_st     = SFP_REG(SFP1_OFF_STATUS);
    uint32_t sfp1_lp     = SFP_REG(SFP1_OFF_LP_ABILITY);
    uint32_t sfp1_rx_f   = SFP_REG(SFP1_OFF_RX_FRAMES);
    uint32_t sfp1_tx_f   = SFP_REG(SFP1_OFF_TX_FRAMES);
    uint32_t sfp1_rx_b   = SFP_REG(SFP1_OFF_RX_BYTES);
    uint32_t sfp1_tx_b   = SFP_REG(SFP1_OFF_TX_BYTES);
    uint32_t sfp1_rx_e   = SFP_REG(SFP1_OFF_RX_ERRORS);

    uint32_t dma_id     = ethernet_packet_reg(0x10);
    uint32_t dma_st     = ethernet_packet_reg(0x18);
    uint32_t dma_tx_f   = ethernet_packet_reg(0x38);
    uint32_t dma_rx_f   = ethernet_packet_reg(0x3c);
    uint32_t dma_rx_d   = ethernet_packet_reg(0x40);
    uint32_t dma_rx_e   = ethernet_packet_reg(0x44);

    puts_uart("\r\n+============================================================+\r\n");
    puts_uart("| Tang Mega 138K Pro - Dual SFP (10G/1G) Status Report       |\r\n");
    puts_uart("+============================================================+\r\n");
    puts_uart("  [SFP0: 10GBASE-R Optical Port]\r\n");
    puts_uart("    Peripheral ID   : 0x"); hex32(sfp0_id);
    puts_uart((sfp0_id == 0xae35010a) ? " (OK)\r\n" : " (FAIL)\r\n");
    puts_uart("    Control Reg     : 0x"); hex32(sfp0_ctrl); puts_uart("\r\n");
    puts_uart("    Status Reg      : 0x"); hex32(sfp0_st); puts_uart("\r\n");
    puts_uart("      - PLL Lock    : "); puts_uart((sfp0_st & (1 << 0)) ? "LOCKED\r\n" : "UNLOCKED\r\n");
    puts_uart("      - Block Lock  : "); puts_uart((sfp0_st & (1 << 1)) ? "LOCKED\r\n" : "UNLOCKED\r\n");
    puts_uart("      - 10G PCS Link: "); puts_uart((sfp0_st & (1 << 2)) ? "UP (10.3125 Gbps)\r\n" : "DOWN\r\n");
    puts_uart("      - Module LOS  : "); puts_uart((sfp0_st & (1 << 3)) ? "LOS ASSERTED (NO LIGHT)\r\n" : "SIGNAL PRESENT\r\n");
    puts_uart("    MAC Counters    : RX="); dec32(sfp0_rx_f); puts_uart(" pkts ("); dec32(sfp0_rx_b); puts_uart(" bytes, "); dec32(sfp0_rx_e); puts_uart(" errs)");
    puts_uart(" | TX="); dec32(sfp0_tx_f); puts_uart(" pkts ("); dec32(sfp0_tx_b); puts_uart(" bytes)\r\n");

    puts_uart("\r\n  [SFP1: 1G BASE-X / SGMII Port]\r\n");
    puts_uart("    Peripheral ID   : 0x"); hex32(sfp1_id);
    puts_uart((sfp1_id == 0xae350001) ? " (OK)\r\n" : " (FAIL)\r\n");
    puts_uart("    Control Reg     : 0x"); hex32(sfp1_ctrl); puts_uart("\r\n");
    puts_uart("    Status Reg      : 0x"); hex32(sfp1_st); puts_uart("\r\n");
    puts_uart("      - Mode        : "); puts_uart((sfp1_st & (1 << 9)) ? "SGMII (Copper)\r\n" : "1000BASE-X (Optical)\r\n");
    puts_uart("      - 1G Link     : "); puts_uart((sfp1_st & (1 << 1)) ? "UP (1.25 Gbps)\r\n" : "DOWN\r\n");
    puts_uart("      - Auto-Neg    : "); puts_uart((sfp1_st & (1 << 2)) ? "COMPLETE\r\n" : "IN PROGRESS / DISABLED\r\n");
    puts_uart("      - Module LOS  : "); puts_uart((sfp1_st & (1 << 3)) ? "LOS ASSERTED\r\n" : "SIGNAL PRESENT\r\n");
    puts_uart("      - RX Sync     : "); puts_uart((sfp1_st & (1 << 4)) ? "ACQUIRED\r\n" : "SEARCHING\r\n");
    puts_uart("      - Remote Fault: "); puts_uart((sfp1_st & (1 << 5)) ? "FAULT DETECTED\r\n" : "OK\r\n");
    puts_uart("    Partner Ability : 0x"); hex16((uint16_t)sfp1_lp); puts_uart("\r\n");
    puts_uart("    MAC Counters    : RX="); dec32(sfp1_rx_f); puts_uart(" pkts ("); dec32(sfp1_rx_b); puts_uart(" bytes, "); dec32(sfp1_rx_e); puts_uart(" errs)");
    puts_uart(" | TX="); dec32(sfp1_tx_f); puts_uart(" pkts ("); dec32(sfp1_tx_b); puts_uart(" bytes)\r\n");

    puts_uart("\r\n  [LiteEth EthernetDMA Engine]\r\n");
    puts_uart("    Engine ID       : 0x"); hex32(dma_id);
    puts_uart((dma_id == 0xae350e03) ? " (OK)\r\n" : " (FAIL)\r\n");
    puts_uart("    Active Route    : ");
    if (active_port == 1) puts_uart("SFP0 (10GBASE-R)\r\n");
    else if (active_port == 2) puts_uart("SFP1 (1G BASE-X / SGMII)\r\n");
    else puts_uart("RGMII (1G Onboard PHY)\r\n");
    puts_uart("    DMA Status      : 0x"); hex32(dma_st); puts_uart("\r\n");
    puts_uart("    DMA Packets     : RX="); dec32(dma_rx_f); puts_uart(" (drops: "); dec32(dma_rx_d); puts_uart(", errors: "); dec32(dma_rx_e); puts_uart(")");
    puts_uart(" | TX="); dec32(dma_tx_f); puts_uart("\r\n");
    puts_uart("+============================================================+\r\n");
}

static void send_dma_udp(uint32_t seq) {
    uint8_t *tx_buf = 0;
    int acquired = ethernet_dma_tx_acquire(&tx_buf);
    if (acquired != 1) {
        puts_uart("[DMA TX FAIL] Buffer acquire returned "); dec32(acquired); puts_uart("\r\n");
        return;
    }

    uint8_t dst_mac[6] = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff};
    uint8_t dst_ip[4]  = {192, 0, 2, 1};

    /* Ethernet Header (14 bytes) */
    memcpy(tx_buf, dst_mac, 6);
    memcpy(tx_buf + 6, BOARD_MAC, 6);
    tx_buf[12] = 0x08; /* EtherType: IPv4 */
    tx_buf[13] = 0x00;

    /* IPv4 Header (20 bytes) */
    tx_buf[14] = 0x45;
    tx_buf[15] = 0x00;
    tx_buf[18] = (uint8_t)(seq >> 8);
    tx_buf[19] = (uint8_t)(seq);
    tx_buf[20] = 0x40; /* DF */
    tx_buf[21] = 0x00;
    tx_buf[22] = 64;   /* TTL */
    tx_buf[23] = 17;   /* UDP */
    tx_buf[24] = 0x00;
    tx_buf[25] = 0x00;
    memcpy(tx_buf + 26, BOARD_IP, 4);
    memcpy(tx_buf + 30, dst_ip, 4);

    /* UDP Header (8 bytes) */
    tx_buf[34] = 0x13; tx_buf[35] = 0x88; /* Src: 5000 */
    tx_buf[36] = 0x13; tx_buf[37] = 0x88; /* Dst: 5000 */
    tx_buf[40] = 0x00; tx_buf[41] = 0x00;

    /* Payload */
    char msg[64];
    int msg_len = 0;
    const char *prefix = (active_port == 1) ?
        "Tang Mega 138K Pro SFP0 10G Packet #" :
        "Tang Mega 138K Pro SFP1 1G Packet #";
    while (prefix[msg_len]) { msg[msg_len] = prefix[msg_len]; msg_len++; }
    char num_buf[10]; int ni = 0;
    uint32_t tmp = seq;
    if (tmp == 0) num_buf[ni++] = '0';
    while (tmp > 0) { num_buf[ni++] = '0' + (tmp % 10); tmp /= 10; }
    while (ni > 0) msg[msg_len++] = num_buf[--ni];
    msg[msg_len++] = '\n';

    memcpy(tx_buf + 42, msg, msg_len);

    uint16_t udp_len = 8 + msg_len;
    tx_buf[38] = (uint8_t)(udp_len >> 8);
    tx_buf[39] = (uint8_t)(udp_len);

    uint16_t ip_len = 20 + udp_len;
    tx_buf[16] = (uint8_t)(ip_len >> 8);
    tx_buf[17] = (uint8_t)(ip_len);

    uint16_t ip_csum = ip_checksum(tx_buf + 14, 20);
    tx_buf[24] = (uint8_t)(ip_csum);
    tx_buf[25] = (uint8_t)(ip_csum >> 8);

    unsigned total_frame_len = 14 + ip_len;
    if (total_frame_len < 60) total_frame_len = 60;

    int submit = ethernet_dma_tx_submit(total_frame_len);
    if (submit == 0) {
        puts_uart("[DMA TX OK] Frame #"); dec32(seq);
        puts_uart(" ("); dec32(total_frame_len); puts_uart(" bytes)\r\n");
    } else {
        puts_uart("[DMA TX FAIL] Code: "); dec32(submit); puts_uart("\r\n");
    }
}

static int verbose_rx = 0;

static void handle_rx_packet(const uint8_t *pkt, unsigned len) {
    if (len < 14) return;

    uint16_t ethertype = ((uint16_t)pkt[12] << 8) | pkt[13];

    if (verbose_rx) {
        puts_uart("[DMA RX] Len: "); dec32(len);
        puts_uart(" bytes | Src: "); print_mac(pkt + 6);
        puts_uart(" | Dst: "); print_mac(pkt);
        puts_uart(" | Type: 0x"); hex16(ethertype); puts_uart("\r\n");
    }

    /* 1. ARP Request -> Reply */
    if (ethertype == 0x0806 && len >= 42) {
        uint16_t op = ((uint16_t)pkt[20] << 8) | pkt[21];
        if (op == 1 && memcmp(pkt + 38, BOARD_IP, 4) == 0) {
            uint8_t *tx_buf = 0;
            if (ethernet_dma_tx_acquire(&tx_buf) == 1) {
                memcpy(tx_buf, pkt + 6, 6);       /* Dst MAC: sender */
                memcpy(tx_buf + 6, BOARD_MAC, 6);  /* Src MAC: our MAC */
                tx_buf[12] = 0x08; tx_buf[13] = 0x06; /* EtherType: ARP */
                tx_buf[14] = 0x00; tx_buf[15] = 0x01; /* HW: Ethernet */
                tx_buf[16] = 0x08; tx_buf[17] = 0x00; /* Proto: IPv4 */
                tx_buf[18] = 6;    tx_buf[19] = 4;    /* HW len, Proto len */
                tx_buf[20] = 0x00; tx_buf[21] = 0x02; /* Op: Reply */
                memcpy(tx_buf + 22, BOARD_MAC, 6);
                memcpy(tx_buf + 28, BOARD_IP, 4);
                memcpy(tx_buf + 32, pkt + 6, 6);      /* Target MAC */
                memcpy(tx_buf + 38, pkt + 28, 4);     /* Target IP */
                unsigned arp_len = 42;
                if (arp_len < 60) {
                    memset(tx_buf + 42, 0, 60 - 42);
                    arp_len = 60;
                }
                ethernet_dma_tx_submit(arp_len);
                if (verbose_rx) puts_uart("  -> Sent ARP Reply\r\n");
            }
        }
        return;
    }

    /* 2. IPv4 Packet */
    if (ethertype == 0x0800 && len >= 34) {
        const uint8_t *ip_hdr = pkt + 14;
        uint8_t proto = ip_hdr[9];
        const uint8_t *dst_ip = ip_hdr + 16;
        if (memcmp(dst_ip, BOARD_IP, 4) != 0) return;

        /* ICMP Echo Request -> Reply */
        if (proto == 1 && len >= 38) {
            const uint8_t *icmp = ip_hdr + 20;
            uint8_t type = icmp[0];
            uint8_t code = icmp[1];
            if (type == 8 && code == 0) {
                uint8_t *tx_buf = 0;
                if (ethernet_dma_tx_acquire(&tx_buf) == 1) {
                    memcpy(tx_buf, pkt, len);
                    memcpy(tx_buf, pkt + 6, 6);
                    memcpy(tx_buf + 6, BOARD_MAC, 6);
                    memcpy(tx_buf + 26, BOARD_IP, 4);
                    memcpy(tx_buf + 30, ip_hdr + 12, 4);
                    uint8_t *tx_icmp = tx_buf + 34;
                    tx_icmp[0] = 0; /* Echo Reply */
                    tx_icmp[2] = 0; tx_icmp[3] = 0; /* Clear checksum */
                    unsigned icmp_len = len - 34;
                    uint16_t csum = ip_checksum(tx_icmp, icmp_len);
                    tx_icmp[2] = (uint8_t)(csum);
                    tx_icmp[3] = (uint8_t)(csum >> 8);
                    unsigned reply_len = len;
                    if (reply_len < 60) reply_len = 60;
                    ethernet_dma_tx_submit(reply_len);
                    if (verbose_rx) puts_uart("  -> Sent ICMP Ping Reply\r\n");
                }
            }
            return;
        }

        /* UDP Echo (Port 5000) */
        if (proto == 17 && len >= 42) {
            const uint8_t *udp = ip_hdr + 20;
            uint16_t dst_port = ((uint16_t)udp[2] << 8) | udp[3];
            if (dst_port == 5000) {
                uint16_t src_port = ((uint16_t)udp[0] << 8) | udp[1];
                uint16_t ulen = ((uint16_t)udp[4] << 8) | udp[5];
                uint8_t *tx_buf = 0;
                if (ethernet_dma_tx_acquire(&tx_buf) == 1) {
                    memcpy(tx_buf, pkt, len);
                    memcpy(tx_buf, pkt + 6, 6);
                    memcpy(tx_buf + 6, BOARD_MAC, 6);
                    memcpy(tx_buf + 26, BOARD_IP, 4);
                    memcpy(tx_buf + 30, ip_hdr + 12, 4);
                    uint8_t *tx_udp = tx_buf + 34;
                    tx_udp[0] = (uint8_t)(dst_port >> 8);
                    tx_udp[1] = (uint8_t)(dst_port);
                    tx_udp[2] = (uint8_t)(src_port >> 8);
                    tx_udp[3] = (uint8_t)(src_port);
                    tx_udp[4] = (uint8_t)(ulen >> 8);
                    tx_udp[5] = (uint8_t)(ulen);
                    tx_udp[6] = 0; tx_udp[7] = 0; /* UDP Checksum optional */
                    uint8_t *tx_ip = tx_buf + 14;
                    uint16_t ip_total_len = 20 + ulen;
                    tx_ip[2] = (uint8_t)(ip_total_len >> 8);
                    tx_ip[3] = (uint8_t)(ip_total_len);
                    tx_buf[24] = 0; tx_buf[25] = 0;
                    uint16_t ipcsum = ip_checksum(tx_ip, 20);
                    tx_buf[24] = (uint8_t)(ipcsum);
                    tx_buf[25] = (uint8_t)(ipcsum >> 8);
                    unsigned reply_len = 14 + ip_total_len;
                    if (reply_len < 60) reply_len = 60;
                    ethernet_dma_tx_submit(reply_len);
                    if (verbose_rx) puts_uart("  -> Sent UDP Echo Reply\r\n");
                }
            }
            return;
        }
    }
}

int main(void) {
    puts_uart("\r\n\r\n====================================================\r\n");
    puts_uart("  Tang Mega 138K Pro - Dual SFP (10G/1G) DMA Engine \r\n");
    puts_uart("====================================================\r\n");
    puts_uart("Board MAC : "); print_mac(BOARD_MAC); puts_uart("\r\n");
    puts_uart("Board IP  : "); print_ip(BOARD_IP); puts_uart("\r\n");

    /* Initialize EthernetDMA descriptor rings in DDR3 */
    puts_uart("Initializing DDR3 EthernetDMA descriptor rings...\r\n");
    _Static_assert(0x4000u + 4u * 1536u <= AE350_DIAG_ETH_BYTES, "rings outside diag.eth");
    const struct ethernet_ring_config ring = {
        .tx         = (struct ethernet_descriptor *)(AE350_DIAG_ETH_BASE + 0x0000u),
        .rx         = (struct ethernet_descriptor *)(AE350_DIAG_ETH_BASE + 0x1000u),
        .tx_buffers = (uint8_t *)(AE350_DIAG_ETH_BASE + 0x2000u),
        .rx_buffers = (uint8_t *)(AE350_DIAG_ETH_BASE + 0x4000u),
        .count      = 4,
        .stride     = 1536
    };
    int init_rc = ethernet_dma_init(&ring);
    if (init_rc != 0) {
        puts_uart("[FATAL] ethernet_dma_init failed with code ");
        dec32(init_rc); puts_uart("\r\n");
        return 1;
    }
    puts_uart("[OK] EthernetDMA rings configured and active.\r\n");

    /* Default to SFP0 10G */
    select_dma_port(1);
    print_status();

    puts_uart("\r\nCommands:\r\n");
    puts_uart("  's' : Print status report\r\n");
    puts_uart("  '0' : Select SFP0 (10GBASE-R optical)\r\n");
    puts_uart("  '1' : Select SFP1 (1G BASE-X optical / SGMII copper)\r\n");
    puts_uart("  'r' : Select RGMII (1G onboard PHY)\r\n");
    puts_uart("  'm' : Toggle SFP1 mode (1000BASE-X <-> SGMII)\r\n");
    puts_uart("  'g' : Trigger hardware line-rate packet generator\r\n");
    puts_uart("  't' : Send test UDP packet via DMA\r\n");
    puts_uart("  'c' : Clear hardware frame counters\r\n");
    puts_uart("  'v' : Toggle verbose RX packet logging\r\n");

    uint32_t udp_seq = 0;

    while (1) {
        /* Poll DMA RX descriptors */
        const uint8_t *rx_buf = 0;
        unsigned rx_len = 0;
        int has_pkt = ethernet_dma_rx_acquire(&rx_buf, &rx_len);
        if (has_pkt > 0 && rx_buf != 0 && rx_len > 0) {
            handle_rx_packet(rx_buf, rx_len);
            ethernet_dma_rx_release();
        }

        /* Check for UART user commands */
        uint32_t c;
        if (getc_nonblocking(&c)) {
            if (c == 's' || c == 'S') {
                print_status();
            } else if (c == '0') {
                select_dma_port(1);
            } else if (c == '1') {
                select_dma_port(2);
            } else if (c == 'r' || c == 'R') {
                select_dma_port(0);
            } else if (c == 'm' || c == 'M') {
                toggle_sfp1_mode();
            } else if (c == 't' || c == 'T') {
                send_dma_udp(++udp_seq);
            } else if (c == 'c' || c == 'C') {
                if (active_port == 1) {
                    uint32_t c0 = SFP_REG(SFP0_OFF_CTRL);
                    SFP_REG(SFP0_OFF_CTRL) = c0 | (1 << 4);
                } else if (active_port == 2) {
                    uint32_t c1 = SFP_REG(SFP1_OFF_CTRL);
                    SFP_REG(SFP1_OFF_CTRL) = c1 | (1 << 4);
                }
                puts_uart("[OK] Hardware statistics counters cleared.\r\n");
            } else if (c == 'g' || c == 'G') {
                if (active_port == 1) {
                    uint32_t c0 = SFP_REG(SFP0_OFF_CTRL);
                    SFP_REG(SFP0_OFF_CTRL) = c0 | (1 << 3) | (100 << 16);
                    puts_uart("[PKT GEN] SFP0 10G: Triggered 100 hardware test frames at 10 Gbps\r\n");
                } else if (active_port == 2) {
                    uint32_t c1 = SFP_REG(SFP1_OFF_CTRL);
                    SFP_REG(SFP1_OFF_CTRL) = c1 | (1 << 3) | (100 << 16);
                    puts_uart("[PKT GEN] SFP1 1G: Triggered 100 hardware test frames at 1 Gbps\r\n");
                } else {
                    puts_uart("[PKT GEN] Packet generator only available on SFP ports.\r\n");
                }
            } else if (c == 'v' || c == 'V') {
                verbose_rx = !verbose_rx;
                puts_uart(verbose_rx ? "[VERBOSE RX ON]\r\n" : "[VERBOSE RX OFF]\r\n");
            } else if (c == 'q' || c == 'Q' || c == '!') {
                puts_uart("[RESET TO BOOTLOADER]\r\n");
                asm volatile("fence.i; jr %0" :: "r"(AE350_ROM_BASE));
            }
        }
    }

    return 0;
}
