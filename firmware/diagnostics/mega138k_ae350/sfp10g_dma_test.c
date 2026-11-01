/* Tang Mega 138K Pro - SFP+ 10G Ethernet DMA Diagnostic Payload
 *
 * Verifies end-to-end 10GBASE-R Ethernet DMA operations:
 * - 64-bit 10G MAC @ 156.25 MHz <-> SFP10GDMABridge <-> 32-bit EthernetDMA @ 50 MHz
 * - DDR3 autonomous descriptor rings (OWN/DONE/ERROR handshakes)
 * - DMA packet transmission (Store-and-forward into 10G line rate)
 * - DMA packet reception and DDR payload buffering
 * - ARP reply, ICMP Ping reply, and UDP Echo over 10G optical link
 */
#include <stdint.h>
#include <string.h>
#include "ae350_address_map.h"
#include "ethernet_dma.h"

#define UART2_BASE   0xf0300000u
#define UART_DATA    0x20u
#define UART_LSR     0x34u
#define UART_REG(o)  (*(volatile uint32_t *)(UART2_BASE + (o)))

#define SFP_BASE     0xf80b0000u
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

static void delay_ms(uint32_t ms) {
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

static void print_status(void) {
    uint32_t sfp_id     = SFP_REG(SFP_OFF_ID);
    uint32_t sfp_ctrl   = SFP_REG(SFP_OFF_CTRL);
    uint32_t sfp_st     = SFP_REG(SFP_OFF_STATUS);
    uint32_t sfp_rx_f   = SFP_REG(SFP_OFF_RX_FRAMES);
    uint32_t sfp_tx_f   = SFP_REG(SFP_OFF_TX_FRAMES);
    uint32_t sfp_rx_b   = SFP_REG(SFP_OFF_RX_BYTES);
    uint32_t sfp_tx_b   = SFP_REG(SFP_OFF_TX_BYTES);
    uint32_t sfp_rx_e   = SFP_REG(SFP_OFF_RX_ERRORS);

    uint32_t dma_id     = ethernet_packet_reg(0x10);
    uint32_t dma_st     = ethernet_packet_reg(0x18);
    uint32_t dma_tx_f   = ethernet_packet_reg(0x38);
    uint32_t dma_rx_f   = ethernet_packet_reg(0x3c);
    uint32_t dma_rx_d   = ethernet_packet_reg(0x40);
    uint32_t dma_rx_e   = ethernet_packet_reg(0x44);

    puts_uart("\r\n+------------------------------------------------+\r\n");
    puts_uart("| SFP+ 10G & Ethernet DMA Link Status Report     |\r\n");
    puts_uart("+------------------------------------------------+\r\n");
    puts_uart("  [10G SerDes & MAC Hardware Layer]\r\n");
    puts_uart("    SFP ID          : 0x"); hex32(sfp_id);
    puts_uart((sfp_id == 0xae35010a) ? " (OK)\r\n" : " (FAIL)\r\n");
    puts_uart("    SFP Control     : 0x"); hex32(sfp_ctrl); puts_uart("\r\n");
    puts_uart("    SFP Status      : 0x"); hex32(sfp_st); puts_uart("\r\n");
    puts_uart("      - PLL Lock    : "); puts_uart((sfp_st & (1 << 0)) ? "LOCKED\r\n" : "UNLOCKED\r\n");
    puts_uart("      - Block Lock  : "); puts_uart((sfp_st & (1 << 1)) ? "LOCKED\r\n" : "UNLOCKED\r\n");
    puts_uart("      - 10G PCS Link: "); puts_uart((sfp_st & (1 << 2)) ? "UP (10 Gbps)\r\n" : "DOWN\r\n");
    puts_uart("      - SFP Module  : "); puts_uart((sfp_st & (1 << 3)) ? "NO SIGNAL\r\n" : "OPTICAL PRESENT\r\n");
    puts_uart("      - DMA Routing : "); puts_uart((sfp_st & (1 << 8)) ? "ROUTED TO 10G SFP+\r\n" : "ROUTED TO RGMII\r\n");
    puts_uart("    MAC RX Frames   : "); dec32(sfp_rx_f); puts_uart(" ("); dec32(sfp_rx_b); puts_uart(" bytes, "); dec32(sfp_rx_e); puts_uart(" errs)\r\n");
    puts_uart("    MAC TX Frames   : "); dec32(sfp_tx_f); puts_uart(" ("); dec32(sfp_tx_b); puts_uart(" bytes)\r\n");

    puts_uart("  [LiteEth EthernetDMA Descriptor Engine]\r\n");
    puts_uart("    DMA Engine ID   : 0x"); hex32(dma_id);
    puts_uart((dma_id == 0xae350e03) ? " (OK)\r\n" : " (FAIL)\r\n");
    puts_uart("    DMA Link Status : 0x"); hex32(dma_st); puts_uart("\r\n");
    puts_uart("    DMA RX Frames   : "); dec32(dma_rx_f); puts_uart(" (drops: "); dec32(dma_rx_d); puts_uart(", errors: "); dec32(dma_rx_e); puts_uart(")\r\n");
    puts_uart("    DMA TX Frames   : "); dec32(dma_tx_f); puts_uart("\r\n");
    puts_uart("+------------------------------------------------+\r\n");
}

static void send_dma_udp(uint32_t seq) {
    uint8_t *tx_buf = 0;
    int acquired = ethernet_dma_tx_acquire(&tx_buf);
    if (acquired != 1) {
        puts_uart("[DMA TX FAIL] Buffer acquire returned "); dec32(acquired); puts_uart("\r\n");
        return;
    }

    /* Target: host enp1s0f0 (broadcast or 192.0.2.1) */
    uint8_t dst_mac[6] = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff};
    uint8_t dst_ip[4]  = {192, 0, 2, 1};

    /* Ethernet Header (14 bytes) */
    memcpy(tx_buf, dst_mac, 6);
    memcpy(tx_buf + 6, BOARD_MAC, 6);
    tx_buf[12] = 0x08; /* EtherType: IPv4 */
    tx_buf[13] = 0x00;

    /* IPv4 Header (20 bytes) */
    tx_buf[14] = 0x45; /* Version=4, IHL=5 */
    tx_buf[15] = 0x00; /* DSCP/ECN */
    /* Total length will be set later */
    tx_buf[18] = (uint8_t)(seq >> 8); /* ID */
    tx_buf[19] = (uint8_t)(seq);
    tx_buf[20] = 0x40; /* Flags: Don't Fragment */
    tx_buf[21] = 0x00;
    tx_buf[22] = 64;   /* TTL */
    tx_buf[23] = 17;   /* Protocol: UDP */
    tx_buf[24] = 0x00; /* Checksum placeholder */
    tx_buf[25] = 0x00;
    memcpy(tx_buf + 26, BOARD_IP, 4);
    memcpy(tx_buf + 30, dst_ip, 4);

    /* UDP Header (8 bytes) */
    tx_buf[34] = 0x13; tx_buf[35] = 0x88; /* Src Port: 5000 */
    tx_buf[36] = 0x13; tx_buf[37] = 0x88; /* Dst Port: 5000 */
    /* UDP length placeholder */
    tx_buf[40] = 0x00; tx_buf[41] = 0x00; /* UDP Checksum (0 = optional) */

    /* Payload */
    char msg[64];
    int msg_len = 0;
    const char *prefix = "AE350 Tang Mega 138K 10G SFP+ DMA Packet #";
    while (prefix[msg_len]) { msg[msg_len] = prefix[msg_len]; msg_len++; }
    /* Append decimal seq */
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
    if (total_frame_len < 60) total_frame_len = 60; /* Minimum Ethernet frame */

    int submit = ethernet_dma_tx_submit(total_frame_len);
    if (submit == 0) {
        puts_uart("[DMA TX SUBMIT OK] Frame #"); dec32(seq);
        puts_uart(" ("); dec32(total_frame_len); puts_uart(" bytes)\r\n");
    } else {
        puts_uart("[DMA TX SUBMIT FAIL] Code: "); dec32(submit); puts_uart("\r\n");
    }
}

static int verbose_rx = 0; /* Default 0 for low-latency line-rate benchmarking; press 'v' to toggle */

static void handle_rx_packet(const uint8_t *pkt, unsigned len) {
    if (len < 14) return;

    uint16_t ethertype = ((uint16_t)pkt[12] << 8) | pkt[13];

    if (verbose_rx) {
        puts_uart("[DMA RX] Frame len: "); dec32(len);
        puts_uart(" bytes | Src MAC: "); print_mac(pkt + 6);
        puts_uart(" | Dst MAC: "); print_mac(pkt);
        puts_uart(" | Type: 0x"); hex16(ethertype); puts_uart("\r\n");
    }

    /* Handle ARP (0x0806) */
    if (ethertype == 0x0806 && len >= 42) {
        uint16_t op = ((uint16_t)pkt[20] << 8) | pkt[21];
        if (op == 1 && memcmp(pkt + 38, BOARD_IP, 4) == 0) {
            uint8_t *tx_buf = 0;
            if (ethernet_dma_tx_acquire(&tx_buf) == 1) {
                /* Ethernet header */
                memcpy(tx_buf, pkt + 6, 6); /* Dst = Requestor MAC */
                memcpy(tx_buf + 6, BOARD_MAC, 6); /* Src = Our MAC */
                tx_buf[12] = 0x08; tx_buf[13] = 0x06;
                /* ARP payload */
                tx_buf[14] = 0x00; tx_buf[15] = 0x01; /* HW: Ethernet */
                tx_buf[16] = 0x08; tx_buf[17] = 0x00; /* Proto: IPv4 */
                tx_buf[18] = 6; tx_buf[19] = 4;       /* Addr lengths */
                tx_buf[20] = 0x00; tx_buf[21] = 0x02; /* Op: Reply */
                memcpy(tx_buf + 22, BOARD_MAC, 6);
                memcpy(tx_buf + 28, BOARD_IP, 4);
                memcpy(tx_buf + 32, pkt + 6, 6);      /* Target MAC */
                memcpy(tx_buf + 38, pkt + 28, 4);     /* Target IP */
                memset(tx_buf + 42, 0, 18);          /* Pad to 60 bytes */
                ethernet_dma_tx_submit(60);
            }
            if (verbose_rx) {
                puts_uart("  -> ARP Request for "); print_ip(BOARD_IP);
                puts_uart(" from "); print_ip(pkt + 28); puts_uart(" -- Sent ARP Reply via DMA!\r\n");
            }
        }
    }
    /* Handle IPv4 (0x0800) */
    else if (ethertype == 0x0800 && len >= 34) {
        uint8_t proto = pkt[23];
        const uint8_t *src_ip = pkt + 26;
        const uint8_t *dst_ip = pkt + 30;

        /* ICMP Echo Request (proto == 1) */
        if (proto == 1 && len >= 38 && memcmp(dst_ip, BOARD_IP, 4) == 0) {
            uint8_t icmp_type = pkt[34];
            if (icmp_type == 8) { /* Echo Request */
                uint8_t *tx_buf = 0;
                if (ethernet_dma_tx_acquire(&tx_buf) == 1) {
                    memcpy(tx_buf, pkt, len);
                    /* Swap MACs */
                    memcpy(tx_buf, pkt + 6, 6);
                    memcpy(tx_buf + 6, BOARD_MAC, 6);
                    /* Swap IPs */
                    memcpy(tx_buf + 26, BOARD_IP, 4);
                    memcpy(tx_buf + 30, src_ip, 4);
                    /* Recalculate IP Checksum */
                    tx_buf[24] = 0; tx_buf[25] = 0;
                    uint16_t ip_csum = ip_checksum(tx_buf + 14, 20);
                    tx_buf[24] = (uint8_t)ip_csum;
                    tx_buf[25] = (uint8_t)(ip_csum >> 8);
                    /* ICMP Reply (Type = 0) */
                    tx_buf[34] = 0;
                    /* Recalculate ICMP Checksum */
                    tx_buf[36] = 0; tx_buf[37] = 0;
                    uint16_t icmp_len = len - 34;
                    uint16_t icmp_csum = ip_checksum(tx_buf + 34, icmp_len);
                    tx_buf[36] = (uint8_t)icmp_csum;
                    tx_buf[37] = (uint8_t)(icmp_csum >> 8);
                    ethernet_dma_tx_submit(len);
                }
                if (verbose_rx) {
                    puts_uart("  -> ICMP Ping Request! Echoed reply via 10G DMA!\r\n");
                }
            }
        }
        /* UDP Echo (proto == 17) */
        else if (proto == 17 && len >= 42) {
            uint16_t dst_port = ((uint16_t)pkt[36] << 8) | pkt[37];
            if (dst_port == 5000) {
                uint8_t *tx_buf = 0;
                if (ethernet_dma_tx_acquire(&tx_buf) == 1) {
                    memcpy(tx_buf, pkt, len);
                    /* Swap MACs */
                    memcpy(tx_buf, pkt + 6, 6);
                    memcpy(tx_buf + 6, BOARD_MAC, 6);
                    /* Swap IPs */
                    memcpy(tx_buf + 26, BOARD_IP, 4);
                    memcpy(tx_buf + 30, src_ip, 4);
                    /* Swap Ports */
                    tx_buf[34] = pkt[36]; tx_buf[35] = pkt[37];
                    tx_buf[36] = pkt[34]; tx_buf[37] = pkt[35];
                    /* IP Checksum */
                    tx_buf[24] = 0; tx_buf[25] = 0;
                    uint16_t ip_csum = ip_checksum(tx_buf + 14, 20);
                    tx_buf[24] = (uint8_t)ip_csum;
                    tx_buf[25] = (uint8_t)(ip_csum >> 8);
                    tx_buf[40] = 0; tx_buf[41] = 0; /* Clear UDP checksum */
                    ethernet_dma_tx_submit(len);
                }
                if (verbose_rx) {
                    puts_uart("  -> UDP Port 5000 Packet! Echoed reply via 10G DMA!\r\n");
                }
            }
        }
    }
}

void main(void) {
    puts_uart("\r\n\r\n");
    puts_uart("========================================================\r\n");
    puts_uart(" Tang Mega 138K Pro - SFP+ 10G Ethernet DMA Diagnostic\r\n");
    puts_uart(" Hardware: Gowin GW5AST-138 + Andes AE350 RISC-V Core\r\n");
    puts_uart(" Interface: SFP0 (10GBASE-R @ 10.3125 Gbps)\r\n");
    puts_uart("========================================================\r\n");

    /* 1. Verify SFP+ 10G Peripheral and enable DMA routing */
    uint32_t sfp_id = SFP_REG(SFP_OFF_ID);
    if (sfp_id != 0xae35010a) {
        puts_uart("[FATAL] Invalid SFP APB ID: 0x"); hex32(sfp_id); puts_uart("\r\n");
        return;
    }
    puts_uart("[1/4] SFP+ 10G APB Device Detected (ID: 0xae35010a)\r\n");

    /* Enable DMA routing on SFP (bit 5 = 1) */
    SFP_REG(SFP_OFF_CTRL) = (1 << 5); /* ctrl_dma_en = 1 */
    delay_ms(10);

    uint32_t st = SFP_REG(SFP_OFF_STATUS);
    puts_uart("[2/4] SFP+ Link Status: 0x"); hex32(st);
    if ((st & 0x07) == 0x07) {
        puts_uart(" -> PLL LOCKED + 64b/66b BLOCK LOCK + 10G PCS LINK UP!\r\n");
    } else {
        puts_uart(" -> [WARN] 10G link not fully up yet (Check optical cable/NIC)\r\n");
    }

    /* 2. Initialize LiteEth EthernetDMA descriptor rings in DDR3 */
    puts_uart("[3/4] Initializing DDR3 Descriptor Rings at 0x02000000...\r\n");
    const struct ethernet_ring_config ring = {
        .tx         = (struct ethernet_descriptor *)0x02000000u,
        .rx         = (struct ethernet_descriptor *)0x02001000u,
        .tx_buffers = (uint8_t *)0x02002000u,
        .rx_buffers = (uint8_t *)0x02004000u,
        .count      = 4,
        .stride     = 1536
    };

    int init_ret = ethernet_dma_init(&ring);
    if (init_ret != 0) {
        puts_uart("[FATAL] ethernet_dma_init failed: "); dec32(init_ret); puts_uart("\r\n");
        return;
    }
    puts_uart(" -> EthernetDMA Engine Initialized Successfully!\r\n");

    /* 3. Send Initial Demonstration Packet */
    puts_uart("[4/4] Sending Initial 10G DMA UDP Test Packet...\r\n");
    send_dma_udp(1);

    print_status();

    puts_uart("\r\nListening for Host Ping / UDP Packets (Interactive Menu):\r\n");
    puts_uart("  's' : Print Status & Performance Counters\r\n");
    puts_uart("  't' : Send 10G DMA UDP Test Packet\r\n");
    puts_uart("  'p' : Trigger Hardware Line-Rate Packet Generator (10 packets)\r\n");
    puts_uart("  'c' : Clear Counters\r\n");
    puts_uart("  'q' : Exit Diagnostic Loop\r\n\r\n");

    uint32_t tx_seq = 2;
    uint32_t last_print = mcycle();

    while (1) {
        /* Check for RX packets from 10G MAC via DMA */
        const uint8_t *rx_buf = 0;
        unsigned rx_len = 0;
        int rx_status = ethernet_dma_rx_acquire(&rx_buf, &rx_len);
        if (rx_status > 0) {
            handle_rx_packet(rx_buf, rx_len);
            ethernet_dma_rx_release();
        }

        /* Check for UART keypress */
        uint32_t key = 0;
        if (getc_nonblocking(&key)) {
            if (key == 's' || key == 'S') {
                print_status();
            } else if (key == 't' || key == 'T') {
                send_dma_udp(tx_seq++);
            } else if (key == 'p' || key == 'P') {
                puts_uart("[HW PKT GEN] Triggering 10 Line-Rate Frames...\r\n");
                SFP_REG(SFP_OFF_CTRL) = (1 << 5) | (1 << 3) | (10 << 16);
            } else if (key == 'c' || key == 'C') {
                puts_uart("[STATS] Clearing Counters...\r\n");
                SFP_REG(SFP_OFF_CTRL) = (1 << 5) | (1 << 4);
            } else if (key == 'v' || key == 'V') {
                verbose_rx = !verbose_rx;
                puts_uart("Verbose RX packet logging: ");
                puts_uart(verbose_rx ? "ENABLED\r\n" : "DISABLED (low-latency mode)\r\n");
            } else if (key == 'q' || key == 'Q') {
                puts_uart("[EXIT] Exiting Diagnostic.\r\n");
                break;
            }
        }

        /* Periodic heartbeat every 5 seconds */
        if ((uint32_t)(mcycle() - last_print) > 5u * 800000000u) {
            last_print = mcycle();
            uint32_t dma_rx = ethernet_packet_reg(0x3c);
            uint32_t dma_tx = ethernet_packet_reg(0x38);
            puts_uart("[Heartbeat] DMA RX: "); dec32(dma_rx);
            puts_uart(" frames | DMA TX: "); dec32(dma_tx);
            puts_uart(" frames\r\n");
        }
    }

    ethernet_dma_stop();
    puts_uart("DMA Stopped. Done.\r\n");
}
