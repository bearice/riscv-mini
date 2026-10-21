/* Board I/O and interrupt suite.
 *
 * Digital result words are the contract. Lighting a LED, pressing a key, or
 * looking at the WS2812 is not inferred from this image: physical stays 0.
 * Fabric events are level-merged. The sequential 1000 count is the only
 * place service count is required to equal injected edges.
 */
#include <stdint.h>
#include "ae350_address_map.h"
#include "ae350_dma.h"
#include "board_io.h"
#include "irq.h"

#define UART2 0xf0300000u
#define DONE 0xC4100004u
#define SCHEMA 0x50340001u
#define GUARD 2000000u

enum {
    STAGE, GPIO_ID, IDENTITY, REJECTS, LED_WALK,
    EDGES, SERVICES, MERGE_EDGES, MERGE_SERVICES,
    MASKED_PENDING, MASKED_SERVICES, STORM, STORM_SETTLED,
    GPIO_CLAIMS, GPIO_SOURCE, FABRIC_SOURCE,
    WS_COMPLETED, WS_BUSY, WS_ID, UNHANDLED, RETRIGGER,
    GPIO_MASKED, PHYSICAL, BUTTONS,
    OVERLAP_STATUS, OVERLAP_DMA_BAD, OVERLAP_CPU_BAD, OVERLAP_SERVICES, OVERLAP_BUSY,
    R29, R30, SCHEMA_W
};

/* Above the payload window (DDR code may occupy through 0x80000). */
#define OV_SRC 0x00100000u
#define OV_DST 0x00110000u
#define OV_CPU 0x00120000u
#define OV_BYTES 0x00010000u
#define OV_LIMIT 200000u

static volatile uint32_t *const slot = (volatile uint32_t *)AE350_RESULTS_BASE;
static volatile uint32_t services;
static volatile uint32_t gpio_services;
static volatile uint32_t storm;
static volatile uint32_t storm_cap;
static volatile uint32_t arm_merge;
static uint32_t fabric_source;

static void putc(char c) {
    volatile uint32_t *lsr = (volatile uint32_t *)(UART2 + 0x34u);
    volatile uint32_t *data = (volatile uint32_t *)(UART2 + 0x20u);
    while ((*lsr & 0x20u) == 0u) { }
    *data = (uint8_t)c;
}
static void puts(const char *text) { while (*text) putc(*text++); }

static int wait_services(uint32_t target) {
    for (uint32_t guard = 0; guard < GUARD; guard++) if (services >= target) return 1;
    return 0;
}

static void on_fabric(void *unused) {
    (void)unused;
    if (storm) {
        services++;
        if (services >= storm_cap) board_diag_clear();
        return;
    }
    if (arm_merge) {
        arm_merge = 0;
        board_diag_pulse();
    }
    board_diag_clear();
    services++;
}

static void on_gpio(void *unused) {
    (void)unused;
    gpio_irq_clear(1u << AE350_GPIO_INJECT_BIT);
    gpio_services++;
}

static uint32_t one_bit(uint32_t before, uint32_t after, uint32_t base) {
    uint32_t diff = after & ~before;
    if (diff == 0u || (diff & (diff - 1u)) != 0u) return 0u;
    uint32_t source = base;
    while ((diff & 1u) == 0u) { diff >>= 1; source++; }
    return source;
}

static int pending_bit(uint32_t source) {
    uint32_t word = source < 32u ? plic_pending(0) : plic_pending(1);
    return (word >> (source & 31u)) & 1u;
}

static uint32_t discover_fabric(void) {
    /* mie stays clear. The fabric gate must be open or PLIC never sees the edge.
       A latched gateway bit is claimed after the line falls so it cannot be
       counted again when the CPU interrupt enable is turned on. */
    for (uint32_t source = 1; source < 64u; source++) plic_priority(source, 1u);
    plic_threshold(0);
    irq_unmask_all();
    board_irq_enable(1);
    uint32_t before0 = plic_pending(0), before1 = plic_pending(1);
    board_diag_pulse();
    uint32_t source = 0;
    uint32_t after0 = before0, after1 = before1;
    for (uint32_t guard = 0; guard < GUARD && !source; guard++) {
        after0 = plic_pending(0);
        after1 = plic_pending(1);
        source = one_bit(before0, after0, 0);
        if (!source) source = one_bit(before1, after1, 32);
    }
    slot[OVERLAP_STATUS] = before0;
    slot[OVERLAP_DMA_BAD] = after0;
    slot[OVERLAP_CPU_BAD] = after0 & ~before0;
    slot[OVERLAP_SERVICES] = after1 & ~before1;
    board_diag_clear();
    if (!source) {
        board_irq_enable(0);
        return 0;
    }
    /* Claim ignores a pending source whose enable bit is clear. */
    if (irq_mask(source, 1)) {
        board_irq_enable(0);
        return 0;
    }
    for (uint32_t guard = 0; guard < GUARD; guard++) {
        if (!pending_bit(source)) {
            irq_mask(source, 0);
            board_irq_enable(0);
            return source;
        }
        uint32_t claimed = plic_claim();
        if (claimed) plic_complete(claimed);
        if (claimed && claimed != source) {
            irq_mask(source, 0);
            board_irq_enable(0);
            return 0;
        }
    }
    irq_mask(source, 0);
    board_irq_enable(0);
    return 0;
}

static uint32_t mode_field(unsigned bit) {
    volatile uint32_t *mode = (volatile uint32_t *)(AE350_GPIO_BASE + 0x54u);
    return (mode[bit >> 3] >> ((bit & 7u) * 4u)) & 7u;
}

static int rejects(void) {
    volatile uint32_t *dir = (volatile uint32_t *)(AE350_GPIO_BASE + 0x28u);
    volatile uint32_t *out = (volatile uint32_t *)(AE350_GPIO_BASE + 0x24u);
    volatile uint32_t *pull = (volatile uint32_t *)(AE350_GPIO_BASE + 0x40u);
    volatile uint32_t *pulltype = (volatile uint32_t *)(AE350_GPIO_BASE + 0x44u);
    volatile uint32_t *mode = (volatile uint32_t *)(AE350_GPIO_BASE + 0x54u);
    volatile uint32_t *intr = (volatile uint32_t *)(AE350_GPIO_BASE + 0x50u);
    uint32_t dir_before = *dir, out_before = *out;
    uint32_t pull_before = *pull, type_before = *pulltype, intr_before = *intr;
    uint32_t mode_before[4];
    for (unsigned i = 0; i < 4u; i++) mode_before[i] = mode[i];
    unsigned untouched = 0x5au;
    int range = gpio_set_dir(40u, 1u);
    int direction = gpio_set_dir(AE350_GPIO_BUTTON_BIT, 1u);
    int write = gpio_write(31u, 1u);
    int led_dir = gpio_set_dir(AE350_GPIO_LED_BIT, 1u);
    int missing = board_button_get(AE350_GPIO_BUTTON_COUNT, &untouched);
    int irq_gap = gpio_irq_rising(17u, 1u);
    int irq_led = gpio_irq_rising(AE350_GPIO_LED_BIT, 1u);
    if (range != GPIO_ERR_RANGE || direction != GPIO_ERR_DIR || write != GPIO_ERR_RANGE) return 0;
    if (led_dir != GPIO_ERR_RANGE || missing != GPIO_ERR_RANGE || untouched != 0x5au) return 0;
    if (irq_gap != GPIO_ERR_RANGE || irq_led != GPIO_ERR_RANGE) return 0;
    if (*dir != dir_before || *out != out_before || *pull != pull_before || *pulltype != type_before) return 0;
    if (*intr != intr_before) return 0;
    for (unsigned i = 0; i < 4u; i++) if (mode[i] != mode_before[i]) return 0;
    if (gpio_irq_rising(AE350_GPIO_BUTTON_BIT, 1u) || gpio_irq_rising(AE350_GPIO_INJECT_BIT, 1u)) return 0;
    if (mode_field(AE350_GPIO_BUTTON_BIT) != 6u || mode_field(AE350_GPIO_INJECT_BIT) != 6u) return 0;
    if (gpio_irq_rising(AE350_GPIO_BUTTON_BIT, 0u) || gpio_irq_rising(AE350_GPIO_INJECT_BIT, 0u)) return 0;
    if (*intr != intr_before || *pull != pull_before || *pulltype != type_before) return 0;
    if (board_switch_count() != 0) return 0;
    return 1;
}

static uint32_t led_walk(void) {
    uint32_t seen = 0;
    for (unsigned index = 0; index < AE350_GPIO_LED_COUNT; index++) {
        unsigned on = 2u;
        if (board_led_set(index, 1u) || board_led_get(index, &on) || on != 1u) return seen;
        seen |= 1u << index;
        if (board_led_set(index, 0u) || board_led_get(index, &on) || on != 0u) return seen;
    }
    return seen;
}

static void dma_bind(struct ae350_dma_req *req) {
    req->device = AE350_DMAC_ID;
    req->abi = AE350_ABI_VERSION;
    req->csr_base = AE350_DMAC_BASE;
    req->irq_id = AE350_IRQ_ID;
    req->need_cap = AE350_CAP_DMAC_DECLARED;
    req->channel = 0;
    req->src = 0;
    req->dst = 0;
    req->bytes = 0;
    req->width_bytes = 4u;
    req->src_mode = 0;
    req->dst_mode = 0;
    req->handshake = 0;
    req->llp = 0;
}

/* The service has to be observed while the DMAC channel is still busy.
 * A pulse that is only retired after the copy completes does not count. */
__attribute__((section(".ilm.text"), noinline, aligned(4)))
static int overlap_dma_ddr(void) {
    volatile uint32_t *src = (volatile uint32_t *)OV_SRC;
    volatile uint32_t *dst = (volatile uint32_t *)OV_DST;
    volatile uint32_t *cpu = (volatile uint32_t *)OV_CPU;
    struct ae350_dma_req req;
    uint32_t words = OV_BYTES / 4u;
    uint32_t dma_bad = 0;
    uint32_t cpu_bad = 0;
    uint32_t served = 0;
    uint32_t pulses = 0;
    int status;
    __asm__ volatile("csrc 0x7ca, %0" :: "r"(3) : "memory");
    ae350_dma_reset();
    for (uint32_t i = 0; i < words; i++) {
        src[i] = 0xA5000000u | i;
        dst[i] = 0;
    }
    __asm__ volatile("fence" ::: "memory");
    if (ae350_dma_sync_for_device(OV_SRC, OV_BYTES) || ae350_dma_sync_for_device(OV_DST, OV_BYTES))
        return AE350_DMA_ERR_REACH;
    irq_mask(AE350_PLIC_GPIO_SOURCE, 0);
    irq_mask(fabric_source, 1);
    board_diag_clear();
    board_irq_enable(1);
    irq_global(1);
    dma_bind(&req);
    req.src = OV_SRC;
    req.dst = OV_DST;
    req.bytes = OV_BYTES;
    status = ae350_dma_submit(&req);
    if (status == AE350_DMA_OK) {
        while (ae350_dma_busy(0) && pulses < OV_LIMIT) {
            uint32_t mark = services;
            uint32_t index = pulses & (words - 1u);
            uint32_t pattern = 0xC3000000u | index;
            board_diag_pulse();
            cpu[index] = pattern;
            if (cpu[index] != pattern) cpu_bad++;
            if (ae350_dma_busy(0) && services > mark) served++;
            pulses++;
        }
    }
    irq_global(0);
    board_irq_enable(0);
    if (status == AE350_DMA_OK && ae350_dma_busy(0)) {
        ae350_dma_abort_drain(0);
        status = AE350_DMA_ERR_TIMEOUT;
    } else if (status == AE350_DMA_OK) {
        status = ae350_dma_wait(0);
    }
    if (ae350_dma_sync_for_cpu(OV_DST, OV_BYTES) != AE350_DMA_OK) status = AE350_DMA_ERR_REACH;
    for (uint32_t i = 0; i < words; i++) if (dst[i] != (0xA5000000u | i)) dma_bad++;
    slot[OVERLAP_STATUS] = (uint32_t)status;
    slot[OVERLAP_DMA_BAD] = dma_bad;
    slot[OVERLAP_CPU_BAD] = cpu_bad;
    slot[OVERLAP_SERVICES] = served;
    slot[OVERLAP_BUSY] = pulses;
    board_diag_clear();
    return status == AE350_DMA_OK && dma_bad == 0u && cpu_bad == 0u && served >= 1u && pulses >= 1u;
}

static int ws_check(void) {
    slot[WS_ID] = *(volatile uint32_t *)AE350_WS2812_BASE;
    if (slot[WS_ID] != AE350_WS2812_ID) return 0;
    uint32_t before = board_ws2812_completed();
    if (board_ws2812_set(0, 0, 0)) return 0;
    unsigned busy = 0;
    if (board_ws2812_busy(&busy) || !busy) return 0;
    slot[WS_BUSY] = (uint32_t)board_ws2812_set(1, 2, 3);
    for (uint32_t guard = 0; guard < GUARD; guard++) {
        if (board_ws2812_completed() != before) break;
    }
    slot[WS_COMPLETED] = board_ws2812_completed();
    return slot[WS_BUSY] == GPIO_ERR_BUSY && slot[WS_COMPLETED] == before + 1u;
}

void main(void) {
    for (unsigned i = 0; i < 32u; i++) slot[i] = 0;
    for (uint32_t i = 0; i < AE350_TRAP_RECORD_WORDS; i++) slot[AE350_TRAP_RECORD + i] = 0;
    slot[SCHEMA_W] = SCHEMA;
    slot[STAGE] = 1;
    uint32_t id = 0;
    slot[IDENTITY] = gpio_probe(&id) == GPIO_OK;
    slot[GPIO_ID] = id;
    if (!slot[IDENTITY]) goto done;
    slot[STAGE] = 2;
    slot[REJECTS] = rejects();
    if (!slot[REJECTS]) goto done;
    slot[STAGE] = 3;
    slot[LED_WALK] = led_walk();
    if (slot[LED_WALK] != ((1u << AE350_GPIO_LED_COUNT) - 1u)) goto done;
    slot[STAGE] = 4;
    if (*(volatile uint32_t *)AE350_IO_BASE != AE350_IO_ID) goto done;
    fabric_source = discover_fabric();
    slot[FABRIC_SOURCE] = fabric_source;
    if (!fabric_source || irq_attach(fabric_source, on_fabric, 0) || irq_mask(fabric_source, 1)) goto done;
    services = 0;
    board_diag_clear();
    board_irq_enable(1);
    irq_global(1);
    uint32_t edge_base = board_diag_edges();
    for (uint32_t i = 0; i < 1000u; i++) {
        board_diag_pulse();
        if (!wait_services(i + 1u)) goto done;
    }
    slot[EDGES] = board_diag_edges() - edge_base;
    slot[SERVICES] = services;
    if (slot[EDGES] != 1000u || slot[SERVICES] != 1000u) goto done;
    slot[STAGE] = 5;
    {
        uint32_t edges = board_diag_edges();
        uint32_t base = services;
        arm_merge = 1;
        board_diag_pulse();
        if (!wait_services(base + 1u)) goto done;
        slot[MERGE_EDGES] = board_diag_edges() - edges;
        slot[MERGE_SERVICES] = services - base;
        /* The fabric gateway latches an edge even while this source's PLIC
           enable is clear, and does not fire again while the line stays high. */
        irq_mask(fabric_source, 0);
        board_diag_pulse();
        for (volatile uint32_t spin = 0; spin < 1000u; spin++) { }
        slot[MASKED_PENDING] = (board_event_status() >> 8) & 1u;
        slot[MASKED_SERVICES] = services - (base + slot[MERGE_SERVICES]);
        uint32_t masked_at = services;
        irq_mask(fabric_source, 1);
        if (!wait_services(masked_at + 1u)) goto done;
        slot[RETRIGGER] = services == masked_at + 1u;
        uint32_t storm_base = services;
        storm_cap = storm_base + 4u;
        storm = 1;
        /* The gateway accepts a rising edge and does not repeat a level that
           stays high, so each counted storm service is its own edge. */
        board_diag_pulse();
        while (services < storm_cap) {
            uint32_t now = services;
            if (!wait_services(now + 1u)) goto done;
            if (services < storm_cap) {
                board_diag_clear();
                board_diag_pulse();
            }
        }
        storm = 0;
        slot[STORM] = services - storm_base;
        uint32_t settled = services;
        for (volatile uint32_t spin = 0; spin < 1000u; spin++) { }
        slot[STORM_SETTLED] = services == settled;
    }
    if (slot[MERGE_EDGES] != 2u || slot[MERGE_SERVICES] != 1u || slot[MASKED_PENDING] != 1u ||
        slot[MASKED_SERVICES] != 0u || slot[STORM] < 2u || !slot[STORM_SETTLED]) goto done;
    slot[STAGE] = 6;
    irq_global(0);
    board_irq_enable(0);
    if (irq_attach(AE350_PLIC_GPIO_SOURCE, on_gpio, 0) || irq_mask(AE350_PLIC_GPIO_SOURCE, 1)) goto done;
    if (gpio_irq_rising(AE350_GPIO_INJECT_BIT, 1)) goto done;
    {
        uint32_t before = plic_pending(0);
        *(volatile uint32_t *)(AE350_IO_BASE + 0x0cu) = 2u;
        slot[GPIO_SOURCE] = one_bit(before, plic_pending(0), 0);
        irq_global(1);
        for (uint32_t guard = 0; guard < GUARD && gpio_services == 0u; guard++) { }
        slot[GPIO_CLAIMS] = gpio_services;
        irq_global(0);
        uint32_t claims = gpio_services;
        irq_mask(AE350_PLIC_GPIO_SOURCE, 0);
        gpio_irq_clear(1u << AE350_GPIO_INJECT_BIT);
        *(volatile uint32_t *)(AE350_IO_BASE + 0x0cu) = 0u;
        *(volatile uint32_t *)(AE350_IO_BASE + 0x0cu) = 2u;
        irq_global(1);
        for (volatile uint32_t spin = 0; spin < 1000u; spin++) { }
        irq_global(0);
        slot[GPIO_MASKED] = gpio_services == claims;
        *(volatile uint32_t *)(AE350_IO_BASE + 0x0cu) = 0u;
        gpio_irq_rising(AE350_GPIO_INJECT_BIT, 0);
        gpio_irq_clear(1u << AE350_GPIO_INJECT_BIT);
    }
    if (slot[GPIO_CLAIMS] < 1u || slot[GPIO_SOURCE] != AE350_PLIC_GPIO_SOURCE || !slot[GPIO_MASKED]) goto done;
    slot[STAGE] = 7;
    if (!ws_check()) goto done;
    slot[STAGE] = 8;
    if (!overlap_dma_ddr()) goto done;
    for (unsigned index = 0; index < AE350_GPIO_LED_COUNT; index++) board_led_set(index, 0);
    slot[BUTTONS] = *(volatile uint32_t *)(AE350_IO_BASE + 0x24u);
    slot[UNHANDLED] = irq_unhandled();
    slot[PHYSICAL] = 0;
    if (slot[UNHANDLED] != 0u) goto done;
    slot[STAGE] = DONE;
    puts("IO DIGITAL DONE\r\nPHYSICAL NOT OBSERVED\r\n");
done:
    for (;;) __asm__ volatile("wfi");
}
