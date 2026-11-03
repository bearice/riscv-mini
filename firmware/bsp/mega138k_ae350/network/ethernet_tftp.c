#include "ethernet_tftp.h"
#include "ethernet_dma.h"
#include "ethernet_phy.h"
#include "ae350_address_map.h"
#include <string.h>

#define NET_RING_COUNT 4u
#define NET_STRIDE 1536u
/* Each member starts on its own 64-byte cache line. */
struct net_area {
    struct ethernet_descriptor tx[NET_RING_COUNT], rx[NET_RING_COUNT];
    uint8_t tx_buffers[NET_RING_COUNT * NET_STRIDE], rx_buffers[NET_RING_COUNT * NET_STRIDE];
};
_Static_assert(sizeof(struct net_area) <= AE350_BIOS_NET_BYTES, "BIOS net ring exceeds its DDR range");
_Static_assert(NET_RING_COUNT * sizeof(struct ethernet_descriptor) % 64u == 0 && NET_STRIDE % 64u == 0,
               "BIOS net ring members must not share cache lines");

extern void bios_putc(char c);
extern void bios_puts(const char *s);
extern uint32_t bios_time_ms(void);
extern void bios_poll(void);

static uint8_t host_mac[6];
static uint8_t my_mac[6] = {0x20, 0x12, 0x03, 0x14, 0x05, 0x06};
static uint8_t my_ip[4] = {169, 254, 20, 20};
static uint8_t server_ip[4];

static _Alignas(16) uint8_t rx[1600];
static _Alignas(16) uint8_t tx[1600];
static unsigned peer_ready;
static unsigned local_port;
static unsigned remote_port;
static int net_initialized;

static unsigned get16(const uint8_t *p) { return ((unsigned)p[0] << 8) | p[1]; }
static void set16(uint8_t *p, unsigned n) { p[0] = (uint8_t)(n >> 8); p[1] = (uint8_t)n; }

static uint32_t sum16(const uint8_t *p, unsigned n, uint32_t s) {
    while (n > 1) { s += get16(p); p += 2; n -= 2; }
    if (n) s += (unsigned)*p << 8;
    while (s >> 16) s = (s & 0xffffu) + (s >> 16);
    return s;
}

static unsigned crc16(const uint8_t *p, unsigned n) {
    return (~sum16(p, n, 0)) & 0xffffu;
}

static void ethernet_hdr(unsigned type, const uint8_t dst[6]) {
    memcpy(tx, dst, 6);
    memcpy(tx + 6, my_mac, 6);
    set16(tx + 12, type);
}

static int send_frame(unsigned n) {
    uint8_t *buf = 0;
    uint32_t start = bios_time_ms();
    while (ethernet_dma_tx_acquire(&buf) <= 0) {
        if (bios_time_ms() - start > 1000) return 0;
        bios_poll();
    }
    memcpy(buf, tx, n);
    return ethernet_dma_tx_submit(n) == 0;
}

static int arp_request(const uint8_t target[4]) {
    const uint8_t broadcast[6] = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff};
    memset(tx, 0, 42);
    ethernet_hdr(0x0806, broadcast);
    set16(tx + 14, 1);      /* Hardware: Ethernet */
    set16(tx + 16, 0x0800); /* Protocol: IPv4 */
    tx[18] = 6; tx[19] = 4;
    set16(tx + 20, 1);      /* Opcode: Request */
    memcpy(tx + 22, my_mac, 6);
    memcpy(tx + 28, my_ip, 4);
    memcpy(tx + 38, target, 4);
    return send_frame(42);
}

static void arp_input(unsigned n) {
    if (n < 42 || get16(rx + 14) != 1 || get16(rx + 16) != 0x0800 || rx[18] != 6 || rx[19] != 4)
        return;
    unsigned op = get16(rx + 20);
    if (op == 2 && !memcmp(rx + 28, server_ip, 4) && !memcmp(rx + 38, my_ip, 4)) {
        memcpy(host_mac, rx + 22, 6);
        peer_ready = 1;
    }
    if (op == 1 && !memcmp(rx + 38, my_ip, 4)) {
        /* Answer who-has my_ip */
        memcpy(tx, rx, 42);
        ethernet_hdr(0x0806, rx + 22);
        set16(tx + 20, 2); /* Opcode: Reply */
        memcpy(tx + 32, rx + 22, 6);
        memcpy(tx + 38, rx + 28, 4);
        memcpy(tx + 22, my_mac, 6);
        memcpy(tx + 28, my_ip, 4);
        send_frame(42);
        if (!memcmp(rx + 28, server_ip, 4)) {
            memcpy(host_mac, rx + 22, 6);
            peer_ready = 1;
        }
    }
}

static unsigned receive_frame(unsigned *out_port) {
    const uint8_t *buf = 0;
    unsigned n = 0;
    int rc = ethernet_dma_rx_acquire(&buf, &n);
    if (rc <= 0 || n < 14) return 0;
    if (n > sizeof(rx)) n = sizeof(rx);
    memcpy(rx, buf, n);
    ethernet_dma_rx_release();

    if (get16(rx + 12) == 0x0806) {
        arp_input(n);
        return 0;
    }
    if (n < 42 || memcmp(rx, my_mac, 6) || get16(rx + 12) != 0x0800 ||
        rx[14] != 0x45 || (rx[23] != 17 && rx[23] != 1) || crc16(rx + 14, 20) ||
        memcmp(rx + 26, server_ip, 4) || memcmp(rx + 30, my_ip, 4))
        return 0;

    unsigned total = get16(rx + 16);
    if (rx[23] == 1) { /* ICMP */
        if (total < 28 || n - 14 < total) return 0;
        if (sum16(rx + 34, total - 20, 0) != 0xffffu) return 0;
        *out_port = 0;
        return total - 20;
    }
    /* UDP */
    unsigned ulen = get16(rx + 38);
    if (total < 28 || total > n - 14 || ulen != total - 20 || get16(rx + 36) != local_port || ulen < 8)
        return 0;
    *out_port = get16(rx + 34);
    return *out_port ? ulen - 8 : 0;
}

static int udp_send(const uint8_t *payload, unsigned len, unsigned dst_port) {
    memset(tx, 0, 42);
    ethernet_hdr(0x0800, host_mac);
    tx[14] = 0x45;
    set16(tx + 16, 28 + len);
    tx[22] = 64; /* TTL */
    tx[23] = 17; /* UDP */
    memcpy(tx + 26, my_ip, 4);
    memcpy(tx + 30, server_ip, 4);
    set16(tx + 24, crc16(tx + 14, 20));
    set16(tx + 34, local_port);
    set16(tx + 36, dst_port);
    set16(tx + 38, 8 + len);
    memcpy(tx + 42, payload, len);
    uint32_t c = (~sum16(tx + 34, 8 + len, sum16(tx + 26, 8, 17 + 8 + len))) & 0xffffu;
    set16(tx + 40, c ? c : 0xffffu);
    return send_frame(42 + len);
}

int ae350_net_init(const uint8_t *mac, const uint8_t *ip) {
    if (mac) memcpy(my_mac, mac, 6);
    if (ip) memcpy(my_ip, ip, 4);
    if (net_initialized) return 0;

    unsigned phy = 0;
    uint32_t id = 0;
    if (ethernet_phy_init(&phy, &id)) return -1;
    if (ethernet_phy_gigabit(phy)) return -2;

    struct net_area *area = (struct net_area *)(uintptr_t)AE350_BIOS_NET_BASE;
    const struct ethernet_ring_config ring = {
        .tx = area->tx, .rx = area->rx,
        .tx_buffers = area->tx_buffers, .rx_buffers = area->rx_buffers,
        .count = NET_RING_COUNT, .stride = NET_STRIDE
    };
    if (ethernet_dma_init(&ring)) return -3;
    net_initialized = 1;
    return 0;
}

int ae350_net_stop(void) {
    if (!net_initialized) return 0;
    net_initialized = 0;
    return ethernet_dma_stop();
}

int ae350_tftp_get(const uint8_t server[4], const char *filename, void *dest, unsigned capacity, unsigned *out_len) {
    *out_len = 0;
    if (ae350_net_init(0, 0)) return -1;

    unsigned fn_len = 0;
    while (fn_len < 64 && filename[fn_len]) fn_len++;
    if (!fn_len || fn_len >= 64) return -2;

    memcpy(server_ip, server, 4);
    peer_ready = 0;
    remote_port = 0;
    local_port = 49152 + (bios_time_ms() & 0x3fff);

    /* Construct TFTP RRQ with blksize=1428 */
    uint8_t req[96];
    req[0] = 0; req[1] = 1;
    memcpy(req + 2, filename, fn_len + 1);
    memcpy(req + 3 + fn_len, "octet", 6);
    unsigned req_n = fn_len + 9;
    memcpy(req + req_n, "blksize\0", 8); req_n += 8;
    memcpy(req + req_n, "1428", 4); req_n += 4;
    req[req_n++] = 0;

    bios_puts("TFTP ARP resolving server ");
    uint32_t start = bios_time_ms();
    uint32_t last = start - 1000;
    while (!peer_ready && (bios_time_ms() - start) < 5000) {
        unsigned port = 0;
        if (!receive_frame(&port)) bios_poll();
        if ((bios_time_ms() - last) >= 250) {
            arp_request(server_ip);
            bios_putc('.');
            last = bios_time_ms();
        }
    }
    if (!peer_ready) {
        bios_puts(" TIMEOUT\r\n");
        return -3;
    }
    bios_puts(" OK (MAC ");
    for (int i = 0; i < 6; i++) {
        static const char hex[] = "0123456789abcdef";
        char h[4] = {hex[(host_mac[i] >> 4) & 15], hex[host_mac[i] & 15], (i == 5) ? 0 : ':', 0};
        bios_puts(h);
    }
    bios_puts(")\r\n");

    /* Send RRQ */
    bios_puts("TFTP fetching ");
    bios_puts(filename);
    bios_puts("... ");

    unsigned expected_block = 1;
    unsigned offset = 0;
    unsigned blksize = 512;
    uint8_t ack[4] = {0, 4, 0, 0};
    unsigned retries = 0;

    if (!udp_send(req, req_n, 69)) return -4;
    start = bios_time_ms();
    last = start;

    while ((bios_time_ms() - start) < 60000) {
        unsigned port = 0;
        unsigned n = receive_frame(&port);
        if (n < 4) bios_poll();
        if (n >= 4) {
            unsigned opcode = get16(rx + 42);
            unsigned block = get16(rx + 44);
            if (remote_port && port != remote_port) continue;

            if (opcode == 5) { /* ERROR */
                bios_puts(" TFTP ERROR\r\n");
                return -5;
            }
            if (opcode == 6) { /* OACK */
                if (!remote_port) remote_port = port;
                const uint8_t *opt = rx + 44;
                if (!memcmp(opt, "blksize", 8)) {
                    unsigned v = 0;
                    const uint8_t *p = opt + 8;
                    while (*p >= '0' && *p <= '9') v = v * 10 + (*p++ - '0');
                    if (v >= 18 && v <= 1428) blksize = v;
                }
                set16(ack + 2, 0);
                udp_send(ack, 4, remote_port);
                retries = 0;
                last = bios_time_ms();
                continue;
            }
            if (opcode == 3 && block == expected_block) { /* DATA */
                if (!remote_port) remote_port = port;
                unsigned bytes = n - 4;
                if (bytes > capacity - offset) {
                    bios_puts(" CAPACITY EXCEEDED\r\n");
                    return -6;
                }
                memcpy((uint8_t *)dest + offset, rx + 46, bytes);
                offset += bytes;
                *out_len = offset;
                if ((expected_block % 256) == 0) bios_putc('.');

                set16(ack + 2, block);
                udp_send(ack, 4, remote_port);
                expected_block++;
                retries = 0;
                last = bios_time_ms();

                if (bytes < blksize) { /* Final packet */
                    bios_puts(" DONE (");
                    char sz[16];
                    unsigned tmp = offset, pos = 0;
                    do { sz[pos++] = '0' + (tmp % 10); tmp /= 10; } while (tmp);
                    while (pos) bios_putc(sz[--pos]);
                    bios_puts(" bytes)\r\n");
                    return 0;
                }
            } else if (opcode == 3 && remote_port && block == expected_block - 1) {
                udp_send(ack, 4, remote_port);
            }
        }
        if (bios_time_ms() - last >= 1000) {
            if (++retries > 10) {
                bios_puts(" TFTP TIMEOUT\r\n");
                return -7;
            }
            if (remote_port) {
                udp_send(ack, 4, remote_port);
            } else {
                udp_send(req, req_n, 69);
            }
            last = bios_time_ms();
        }
    }
    bios_puts(" TIMEOUT\r\n");
    return -8;
}

uint32_t ae350_crc32_update(uint32_t previous, const void *buf, unsigned len) {
    uint32_t crc = ~previous;
    const uint8_t *p = (const uint8_t *)buf;
    while (len--) {
        crc ^= *p++;
        for (int k = 0; k < 8; k++)
            crc = (crc >> 1) ^ (0xedb88320u & -(crc & 1));
    }
    return ~crc;
}

uint32_t ae350_crc32(const void *buf, unsigned len) { return ae350_crc32_update(0, buf, len); }
