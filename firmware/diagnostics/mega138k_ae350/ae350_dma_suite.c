/* DMA suite. Channel programming goes through ae350_dma_submit / abort_drain.
 *
 * Bulk scope is 10000 word copies of 1..32 words (increment, fixed source,
 * decrement, and a 4 KiB boundary cross). Byte and halfword are short copies.
 * Wider than a word is rejected, not claimed. IRQ evidence is the published
 * IRQ-page identity, not a PLIC handler.
 */
#include <stdint.h>
#include "ae350_address_map.h"
#include "ae350_dma.h"

#define W(addr) (*(volatile uint32_t *)(uintptr_t)(addr))
#define SRC 0x00040000u
#define DST 0x00050000u
#define CANARY 0x00051000u
#define CROSS_SRC 0x00021FF0u
#define CROSS_DST 0x00031FF0u
#define CH1_SRC 0x00060000u
#define CH1_DST 0x00061000u
#define GUARD 0xA11CE001u
#define LINE 0x00200000u
#define LINE_SRC 0x00201000u
#define LINE_DST 0x00202400u
#define MULTI 0x00220000u
#define MULTI_SRC 0x00221000u
#define PARTIAL 0x00230000u
#define PARTIAL_SRC 0x00231000u
#define HS_SRC 0x00240000u
#define HS_DST 0x00241000u

enum {
    STAGE, BULK_COUNT, BULK_MISMATCH, BULK_GUARDS, BULK_TIMEOUT,
    REJECT_MASK, STATUS_DEVICE, STATUS_ABI, STATUS_BASE,
    CHAIN_OK, DUAL_OK, CYCLIC, LATE_WRITES, CACHE_STALE, CACHE_POLICY,
    STORE_DELTA, VIS_BEFORE, VIS_AFTER, WIDTH_ERRORS, HS_ERRORS,
    HS_MATCHED, HS_ACKS, HS_FULL, HS_ORDER, HS_REUSE, HS_RX,
    HS_STOPPED, DMAC_CFG, FIRST_BAD, STATUS_UNALIGNED, STATUS_WIDTH, SCHEMA
};

static volatile uint32_t *const slot = (volatile uint32_t *)AE350_RESULTS_BASE;
static struct dmac_desc {
    uint32_t ctrl, size, src, srch, dst, dsth, llp, llph;
} chain[2] __attribute__((aligned(32)));

static void bind(struct ae350_dma_req *req) {
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

static uint32_t dc_on(void) {
    uint32_t value;
    __asm__ volatile("csrs 0x7ca, %1\n\tcsrr %0, 0x7ca" : "=r"(value) : "r"(2) : "memory");
    return value;
}

static void dc_off(void) {
    __asm__ volatile("csrc 0x7ca, %0" :: "r"(3) : "memory");
}

static void inval_all(void) {
    __asm__ volatile("csrw 0x7cc, %0" :: "r"(23) : "memory");
}

static uint32_t settle_writes(void) {
    for (volatile uint32_t i = 0; i < 512u; i++) __asm__ volatile("nop");
    return W(AE350_WRITE_COUNT_ADDR);
}

static void note(uint32_t bit, int status, int want) {
    if (status == want) slot[REJECT_MASK] |= bit;
}

__attribute__((section(".ilm.text"), noinline, aligned(4)))
static void rejections(void) {
    struct ae350_dma_req req;
    W(CANARY) = GUARD;
    bind(&req);
    req.src = SRC;
    req.dst = CANARY;
    req.bytes = 0;
    note(1u << 0, ae350_dma_submit(&req), AE350_DMA_ERR_SIZE);
    bind(&req);
    req.src = SRC + 1u;
    req.dst = CANARY;
    req.bytes = 4u;
    slot[STATUS_UNALIGNED] = (uint32_t)ae350_dma_submit(&req);
    bind(&req);
    req.src = AE350_DLM_BASE;
    req.dst = DST;
    req.bytes = 4u;
    note(1u << 2, ae350_dma_submit(&req), AE350_DMA_ERR_REACH);
    bind(&req);
    req.src = SRC;
    req.dst = CANARY;
    req.bytes = 4u;
    /* Bit 31 is unadvertised; the video reader is a supported capability. */
    req.need_cap = 1u << 31;
    note(1u << 3, ae350_dma_submit(&req), AE350_DMA_ERR_CAP);
    bind(&req);
    req.device = 0x12340001u;
    req.src = SRC;
    req.dst = CANARY;
    req.bytes = 4u;
    note(1u << 4, ae350_dma_submit(&req), AE350_DMA_ERR_DEVICE);
    bind(&req);
    req.device = 0xAE350299u;
    req.src = SRC;
    req.dst = CANARY;
    req.bytes = 4u;
    slot[STATUS_DEVICE] = (uint32_t)ae350_dma_submit(&req);
    note(1u << 5, (int)slot[STATUS_DEVICE], AE350_DMA_ERR_DEVICE);
    bind(&req);
    req.abi = 0x00020000u;
    req.src = SRC;
    req.dst = CANARY;
    req.bytes = 4u;
    slot[STATUS_ABI] = (uint32_t)ae350_dma_submit(&req);
    bind(&req);
    req.csr_base = AE350_DMAC_BASE + 4u;
    req.src = SRC;
    req.dst = CANARY;
    req.bytes = 4u;
    slot[STATUS_BASE] = (uint32_t)ae350_dma_submit(&req);
    bind(&req);
    req.irq_id = AE350_IRQ_ID ^ 1u;
    req.src = SRC;
    req.dst = CANARY;
    req.bytes = 4u;
    slot[STATUS_BASE] |= (uint32_t)ae350_dma_submit(&req) << 16;
    bind(&req);
    req.src = SRC;
    req.dst = CANARY;
    req.bytes = 8u;
    req.width_bytes = 8u;
    slot[STATUS_WIDTH] = (uint32_t)ae350_dma_submit(&req);
    note(1u << 6, (int)slot[STATUS_ABI], AE350_DMA_ERR_ABI);
    note(1u << 7, (int)(slot[STATUS_BASE] & 0xffffu), AE350_DMA_ERR_BIND);
    note(1u << 8, (int)(slot[STATUS_BASE] >> 16), AE350_DMA_ERR_BIND);
    note(1u << 9, (int)slot[STATUS_WIDTH], AE350_DMA_ERR_WIDTH);
    if (W(CANARY) == GUARD) note(1u << 1, (int)slot[STATUS_UNALIGNED], AE350_DMA_ERR_ALIGN);
    for (uint32_t i = 0; i < 4u; i++) W(SRC + i * 4u) = 0x600d0000u | i;
    W(DST) = 0;
    W(DST + 4u) = 0;
    W(DST + 8u) = 0;
    W(DST + 12u) = 0;
    __asm__ volatile("fence" ::: "memory");
    bind(&req);
    req.src = SRC;
    req.dst = DST;
    req.bytes = 16u;
    if (ae350_dma_submit(&req) == AE350_DMA_OK && ae350_dma_wait(0) == AE350_DMA_OK &&
        W(DST) == 0x600d0000u && W(DST + 12u) == 0x600d0003u) {
        slot[REJECT_MASK] |= 1u << 10;
    }
    if (W(AE350_DMA_SVC_BASE + 0x2cu) != 0xffffffffu) slot[REJECT_MASK] |= 1u << 11;
}

__attribute__((section(".ilm.text"), noinline, aligned(4)))
static void bulk(void) {
    struct ae350_dma_req req;
    for (uint32_t i = 0; i < 10000u; i++) {
        uint32_t n = (i % 32u) + 1u;
        uint32_t off = (i & 7u) * 4u;
        uint32_t src = SRC + off;
        uint32_t dst = DST + off;
        uint32_t mode = 0;
        if ((i % 64u) == 0u) {
            mode = 2u;
            n = 8u;
            src = SRC;
            dst = DST;
            W(src) = 0xF1000000u | i;
            for (uint32_t w = 0; w < n; w++) W(dst + w * 4u) = 0;
        } else if ((i % 128u) == 1u) {
            mode = 1u;
            for (uint32_t w = 0; w < n; w++) {
                W(SRC + off + w * 4u) = 0xD2000000u | (i << 8) | w;
                W(DST + off + w * 4u) = 0;
            }
            src = SRC + off + (n - 1u) * 4u;
            dst = DST + off + (n - 1u) * 4u;
        } else if ((i % 200u) == 2u) {
            mode = 3u;
            n = 8u;
            src = CROSS_SRC;
            dst = CROSS_DST;
            for (uint32_t w = 0; w < n; w++) {
                W(src + w * 4u) = 0xB4000000u | w;
                W(dst + w * 4u) = 0;
            }
        } else {
            for (uint32_t w = 0; w < n; w++) {
                W(src + w * 4u) = 0xA5000000u | (i << 8) | w;
                W(dst + w * 4u) = 0;
            }
        }
        uint32_t tail = (mode == 1u) ? (DST + off + n * 4u) : (dst + n * 4u);
        W(tail) = GUARD;
        __asm__ volatile("fence" ::: "memory");
        bind(&req);
        req.src = src;
        req.dst = dst;
        req.bytes = n * 4u;
        if (mode == 2u) req.src_mode = 2u;
        if (mode == 1u) { req.src_mode = 1u; req.dst_mode = 1u; }
        if (ae350_dma_submit(&req) != AE350_DMA_OK || ae350_dma_wait(0) != AE350_DMA_OK)
            slot[BULK_TIMEOUT]++;
        for (uint32_t w = 0; w < n; w++) {
            uint32_t got = W((mode == 1u ? DST + off : dst) + w * 4u);
            uint32_t want = mode == 2u ? W(SRC)
                          : mode == 1u ? (0xD2000000u | (i << 8) | w)
                          : mode == 3u ? (0xB4000000u | w)
                          : (0xA5000000u | (i << 8) | w);
            if (got != want) {
                if (slot[BULK_MISMATCH] == 0) slot[FIRST_BAD] = i;
                slot[BULK_MISMATCH]++;
                break;
            }
        }
        if (W(tail) != GUARD) slot[BULK_GUARDS]++;
        slot[BULK_COUNT] = i + 1u;
    }
}

__attribute__((section(".ilm.text"), noinline, aligned(4)))
static void chains(void) {
    struct ae350_dma_req req;
    uint32_t ctrl = (2u << 21) | (2u << 18) | (1u << 3) | (1u << 1);
    uint32_t chain_ok = 1u;
    uint32_t dual_ok = 1u;
    for (uint32_t w = 0; w < 4u; w++) {
        W(SRC + w * 4u) = 0xC1000000u | w;
        W(DST + w * 4u) = 0;
        W(SRC + 0x40u + w * 4u) = 0xC2000000u | w;
        W(DST + 0x40u + w * 4u) = 0;
    }
    chain[0].ctrl = ctrl;
    chain[0].size = 4u;
    chain[0].src = SRC + 0x40u;
    chain[0].dst = DST + 0x40u;
    chain[0].llp = 0;
    chain[0].srch = chain[0].dsth = chain[0].llph = 0;
    __asm__ volatile("fence" ::: "memory");
    bind(&req);
    req.src = SRC;
    req.dst = DST;
    req.bytes = 16u;
    req.llp = (uint32_t)(uintptr_t)&chain[0];
    if (ae350_dma_submit(&req) != AE350_DMA_OK || ae350_dma_wait(0) != AE350_DMA_OK) chain_ok = 0;
    for (uint32_t w = 0; w < 4u; w++) {
        if (W(DST + w * 4u) != (0xC1000000u | w)) chain_ok = 0;
        if (W(DST + 0x40u + w * 4u) != (0xC2000000u | w)) chain_ok = 0;
    }
    for (uint32_t w = 0; w < 4u; w++) {
        W(CH1_SRC + w * 4u) = 0xD0A00000u | w;
        W(CH1_DST + w * 4u) = 0;
        W(SRC + 0x80u + w * 4u) = 0xD0B00000u | w;
        W(DST + 0x80u + w * 4u) = 0;
    }
    __asm__ volatile("fence" ::: "memory");
    bind(&req);
    req.src = CH1_SRC;
    req.dst = CH1_DST;
    req.bytes = 16u;
    if (ae350_dma_submit(&req) != AE350_DMA_OK) dual_ok = 0;
    bind(&req);
    req.channel = 1u;
    req.src = SRC + 0x80u;
    req.dst = DST + 0x80u;
    req.bytes = 16u;
    if (ae350_dma_submit(&req) != AE350_DMA_OK || ae350_dma_wait(0) != AE350_DMA_OK ||
        ae350_dma_wait(1) != AE350_DMA_OK) dual_ok = 0;
    for (uint32_t w = 0; w < 4u; w++) {
        if (W(CH1_DST + w * 4u) != (0xD0A00000u | w)) dual_ok = 0;
        if (W(DST + 0x80u + w * 4u) != (0xD0B00000u | w)) dual_ok = 0;
    }
    chain[0].ctrl = ctrl;
    chain[0].size = 4u;
    chain[0].src = SRC;
    chain[0].dst = DST;
    chain[0].llp = (uint32_t)(uintptr_t)&chain[1];
    chain[1].ctrl = ctrl;
    chain[1].size = 4u;
    chain[1].src = SRC;
    chain[1].dst = DST;
    chain[1].llp = (uint32_t)(uintptr_t)&chain[0];
    chain[0].srch = chain[0].dsth = chain[0].llph = 0;
    chain[1].srch = chain[1].dsth = chain[1].llph = 0;
    W(SRC) = 0xC7C1C7C1u;
    for (uint32_t w = 0; w < 4u; w++) W(DST + w * 4u) = 0;
    W(DST + 16u) = GUARD;
    __asm__ volatile("fence" ::: "memory");
    bind(&req);
    req.src = SRC;
    req.dst = DST;
    req.bytes = 16u;
    req.llp = (uint32_t)(uintptr_t)&chain[0];
    uint32_t cyclic = 0;
    if (ae350_dma_submit(&req) == AE350_DMA_OK) {
        for (uint32_t i = 0; i < 1000u; i++) if (ae350_dma_busy(0)) cyclic |= 1u;
        if (ae350_dma_abort_drain(0) == AE350_DMA_OK && ae350_dma_busy(0) == 0u) cyclic |= 2u;
    }
    uint32_t sample[5];
    for (uint32_t w = 0; w < 5u; w++) sample[w] = W(DST + w * 4u);
    for (volatile uint32_t i = 0; i < 2000u; i++) __asm__ volatile("nop");
    uint32_t late = 0;
    for (uint32_t w = 0; w < 5u; w++) if (W(DST + w * 4u) != sample[w]) late++;
    if ((cyclic & 2u) != 0u && late == 0u && sample[4] == GUARD) cyclic |= 4u;
    slot[LATE_WRITES] = ((cyclic & 2u) != 0u) ? late : 0xffffffffu;
    for (uint32_t w = 0; w < 4u; w++) W(SRC + w * 4u) = 0x0E050000u | w;
    __asm__ volatile("fence" ::: "memory");
    bind(&req);
    req.src = SRC;
    req.dst = DST;
    req.bytes = 16u;
    uint32_t reuse = ae350_dma_submit(&req) == AE350_DMA_OK && ae350_dma_wait(0) == AE350_DMA_OK;
    for (uint32_t w = 0; w < 4u; w++) if (W(DST + w * 4u) != (0x0E050000u | w)) reuse = 0;
    if (reuse && W(DST + 16u) == GUARD) cyclic |= 8u;
    slot[CHAIN_OK] = chain_ok;
    slot[DUAL_OK] = dual_ok;
    slot[CYCLIC] = cyclic;
}

__attribute__((section(".ilm.text"), noinline, aligned(4)))
static void widths(void) {
    struct ae350_dma_req req;
    uint32_t errors = 0;
    for (uint32_t i = 0; i < 8u; i++) ((volatile uint8_t *)(uintptr_t)SRC)[i] = (uint8_t)(0x80u + i);
    for (uint32_t i = 0; i < 8u; i++) ((volatile uint8_t *)(uintptr_t)DST)[i] = 0;
    __asm__ volatile("fence" ::: "memory");
    bind(&req);
    req.src = SRC;
    req.dst = DST;
    req.bytes = 8u;
    req.width_bytes = 1u;
    if (ae350_dma_submit(&req) != AE350_DMA_OK || ae350_dma_wait(0) != AE350_DMA_OK) errors++;
    for (uint32_t i = 0; i < 8u; i++) {
        if (((volatile uint8_t *)(uintptr_t)DST)[i] != (uint8_t)(0x80u + i)) errors++;
    }
    for (uint32_t i = 0; i < 4u; i++) ((volatile uint16_t *)(uintptr_t)SRC)[i] = (uint16_t)(0x9100u + i);
    for (uint32_t i = 0; i < 4u; i++) ((volatile uint16_t *)(uintptr_t)DST)[i] = 0;
    __asm__ volatile("fence" ::: "memory");
    bind(&req);
    req.src = SRC;
    req.dst = DST;
    req.bytes = 8u;
    req.width_bytes = 2u;
    if (ae350_dma_submit(&req) != AE350_DMA_OK || ae350_dma_wait(0) != AE350_DMA_OK) errors++;
    for (uint32_t i = 0; i < 4u; i++) {
        if (((volatile uint16_t *)(uintptr_t)DST)[i] != (uint16_t)(0x9100u + i)) errors++;
    }
    slot[WIDTH_ERRORS] = errors;
}

__attribute__((section(".ilm.text"), noinline, aligned(4)))
static void ownership(void) {
    struct ae350_dma_req req;
    uint32_t flags = 0;
    dc_off();
    inval_all();
    for (uint32_t w = 0; w < 8u; w++) {
        W(LINE + w * 4u) = 0x11111111u;
        W(LINE_SRC + w * 4u) = 0x22222222u;
        W(LINE_DST + w * 4u) = 0;
    }
    __asm__ volatile("fence" ::: "memory");
    (void)dc_on();
    (void)W(LINE);
    for (volatile uint32_t i = 0; i < 512u; i++) __asm__ volatile("nop");
    if (W(LINE) == 0x11111111u) flags |= 1u;
    uint32_t before = settle_writes();
    uint32_t noise_end = settle_writes();
    uint32_t noise = noise_end - before;
    for (uint32_t w = 0; w < 8u; w++) W(LINE + w * 4u) = 0x22222222u;
    uint32_t after = settle_writes();
    slot[STORE_DELTA] = (after - noise_end) - noise;
    __asm__ volatile("fence" ::: "memory");
    bind(&req);
    req.src = LINE;
    req.dst = LINE_DST;
    req.bytes = 32u;
    if (ae350_dma_submit(&req) == AE350_DMA_OK && ae350_dma_wait(0) == AE350_DMA_OK) {
        dc_off();
        slot[VIS_BEFORE] = W(LINE_DST) == 0x22222222u;
        (void)dc_on();
    }
    if (ae350_dma_sync_for_device(LINE, 32u) == AE350_DMA_OK) {
        dc_off();
        for (uint32_t w = 0; w < 8u; w++) W(LINE_DST + w * 4u) = 0;
        __asm__ volatile("fence" ::: "memory");
        (void)dc_on();
        bind(&req);
        req.src = LINE;
        req.dst = LINE_DST;
        req.bytes = 32u;
        if (ae350_dma_submit(&req) == AE350_DMA_OK && ae350_dma_wait(0) == AE350_DMA_OK) {
            dc_off();
            slot[VIS_AFTER] = W(LINE_DST) == 0x22222222u;
        }
    }
    /* DMA seeing the new word before clean means it is already visible.
     * A flat write-counter sample does not turn that into a dirty line. */
    if (slot[VIS_BEFORE] == 1u && slot[VIS_AFTER] == 1u) slot[CACHE_POLICY] = 1u;
    else if (slot[STORE_DELTA] == 0u && slot[VIS_BEFORE] == 0u && slot[VIS_AFTER] == 1u)
        slot[CACHE_POLICY] = 2u;
    dc_off();
    inval_all();
    W(LINE) = 0x11111111u;
    for (uint32_t w = 0; w < 8u; w++) W(LINE_SRC + w * 4u) = 0x33333333u;
    __asm__ volatile("fence" ::: "memory");
    (void)dc_on();
    (void)W(LINE);
    for (volatile uint32_t i = 0; i < 512u; i++) __asm__ volatile("nop");
    bind(&req);
    req.src = LINE_SRC;
    req.dst = LINE;
    req.bytes = 32u;
    if (ae350_dma_submit(&req) == AE350_DMA_OK && ae350_dma_wait(0) == AE350_DMA_OK &&
        W(LINE) == 0x11111111u) flags |= 2u;
    if (ae350_dma_sync_for_cpu(LINE, 4u) == AE350_DMA_OK && W(LINE) == 0x33333333u) flags |= 4u;
    dc_off();
    inval_all();
    for (uint32_t w = 0; w < 24u; w++) {
        W(MULTI + w * 4u) = 0x11111111u;
        W(MULTI_SRC + w * 4u) = 0x44444444u;
    }
    __asm__ volatile("fence" ::: "memory");
    (void)dc_on();
    (void)W(MULTI);
    (void)W(MULTI + 64u);
    for (volatile uint32_t i = 0; i < 512u; i++) __asm__ volatile("nop");
    bind(&req);
    req.src = MULTI_SRC;
    req.dst = MULTI;
    req.bytes = 96u;
    if (ae350_dma_submit(&req) == AE350_DMA_OK && ae350_dma_wait(0) == AE350_DMA_OK &&
        W(MULTI) == 0x11111111u && W(MULTI + 64u) == 0x11111111u) flags |= 8u;
    if (ae350_dma_sync_for_cpu(MULTI, 96u) == AE350_DMA_OK &&
        W(MULTI) == 0x44444444u && W(MULTI + 64u) == 0x44444444u) flags |= 16u;
    dc_off();
    inval_all();
    for (uint32_t w = 0; w < 8u; w++) W(PARTIAL + w * 4u) = 0x11111111u;
    for (uint32_t w = 0; w < 3u; w++) W(PARTIAL_SRC + w * 4u) = 0x55555555u;
    __asm__ volatile("fence" ::: "memory");
    (void)dc_on();
    (void)W(PARTIAL);
    for (volatile uint32_t i = 0; i < 512u; i++) __asm__ volatile("nop");
    bind(&req);
    req.src = PARTIAL_SRC;
    req.dst = PARTIAL + 4u;
    req.bytes = 12u;
    if (ae350_dma_submit(&req) == AE350_DMA_OK && ae350_dma_wait(0) == AE350_DMA_OK &&
        W(PARTIAL + 4u) == 0x11111111u) flags |= 32u;
    if (ae350_dma_sync_for_cpu(PARTIAL + 4u, 12u) == AE350_DMA_OK &&
        W(PARTIAL + 4u) == 0x55555555u && W(PARTIAL) == 0x11111111u) flags |= 64u;
    dc_off();
    inval_all();
    slot[CACHE_STALE] = flags;
}

__attribute__((section(".ilm.text"), noinline, aligned(4)))
static void handshake(void) {
    struct ae350_dma_req req;
    uint32_t errors = 0;
    ae350_dma_reset();
    W(AE350_DMA_SVC_BASE + 0x0cu) = 1u;
    for (uint32_t i = 0; i < 4u; i++) W(HS_SRC + i * 4u) = 0x71000000u | i;
    __asm__ volatile("fence" ::: "memory");
    bind(&req);
    req.need_cap = AE350_CAP_DMA_REQ;
    req.src = HS_SRC;
    req.dst = AE350_LOOPBACK_DATA;
    req.bytes = 16u;
    req.dst_mode = 2u;
    req.handshake = 1u;
    if (ae350_dma_submit(&req) != AE350_DMA_OK) errors |= 1u;
    for (uint32_t i = 0; i < 400u; i++) if (ae350_dma_busy(0) == 0u) errors |= 1u;
    W(AE350_DMA_SVC_BASE + 0x0cu) = 2u;
    if (ae350_dma_wait(0) != AE350_DMA_OK) errors |= 2u;
    uint32_t matched = 0;
    for (uint32_t i = 0; i < 4u && W(AE350_DMA_SVC_BASE + 0x20u) != 0u; i++) {
        if (W(AE350_LOOPBACK_DATA) == (0x71000000u | i)) matched++;
    }
    slot[HS_MATCHED] = matched;
    slot[HS_ACKS] = W(AE350_DMA_SVC_BASE + 0x2cu) & 0xffffu;
    if (matched != 4u) errors |= 4u;
    if (slot[HS_ACKS] != 4u) errors |= 8u;
    for (uint32_t i = 0; i < 24u; i++) W(HS_SRC + i * 4u) = 0x72000000u | i;
    __asm__ volatile("fence" ::: "memory");
    bind(&req);
    req.need_cap = AE350_CAP_DMA_REQ;
    req.src = HS_SRC;
    req.dst = AE350_LOOPBACK_DATA;
    req.bytes = 96u;
    req.dst_mode = 2u;
    req.handshake = 1u;
    if (ae350_dma_submit(&req) != AE350_DMA_OK) errors |= 16u;
    uint32_t level = 0;
    for (uint32_t i = 0; i < 20000u; i++) {
        level = W(AE350_DMA_SVC_BASE + 0x20u);
        if (level == 16u) break;
    }
    slot[HS_FULL] = level;
    if (level != 16u || ae350_dma_busy(0) == 0u) errors |= 16u;
    uint32_t first = W(AE350_LOOPBACK_DATA);
    for (volatile uint32_t i = 0; i < 2000u; i++) __asm__ volatile("nop");
    level = W(AE350_DMA_SVC_BASE + 0x20u);
    uint32_t next = level != 0u ? W(AE350_LOOPBACK_DATA) : 0u;
    slot[HS_ORDER] = (first == 0x72000000u ? 1u : 0u) | (next == 0x72000001u ? 2u : 0u);
    if (level != 16u || slot[HS_ORDER] != 3u) errors |= 32u;
    int drained = ae350_dma_abort_drain(0);
    slot[HS_STOPPED] = (drained == AE350_DMA_OK && ae350_dma_busy(0) == 0u) ? 1u : 0u;
    if (slot[HS_STOPPED] != 1u) errors |= 64u;
    if (slot[HS_STOPPED] == 1u) W(AE350_DMA_SVC_BASE + 0x0cu) = 1u;
    else ae350_dma_reset();
    if (slot[HS_STOPPED] != 1u || W(AE350_DMA_SVC_BASE + 0x20u) != 0u) errors |= 128u;
    W(HS_SRC) = 0x73000001u;
    __asm__ volatile("fence" ::: "memory");
    W(AE350_DMA_SVC_BASE + 0x0cu) = 2u;
    bind(&req);
    req.need_cap = AE350_CAP_DMA_REQ;
    req.src = HS_SRC;
    req.dst = AE350_LOOPBACK_DATA;
    req.bytes = 4u;
    req.dst_mode = 2u;
    req.handshake = 1u;
    if (ae350_dma_submit(&req) != AE350_DMA_OK || ae350_dma_wait(0) != AE350_DMA_OK) errors |= 256u;
    slot[HS_REUSE] = W(AE350_DMA_SVC_BASE + 0x20u) != 0u ? W(AE350_LOOPBACK_DATA) : 0u;
    if (slot[HS_REUSE] != 0x73000001u) errors |= 256u;
    W(AE350_DMA_SVC_BASE + 0x0cu) = 1u;
    for (uint32_t i = 0; i < 32u && W(AE350_DMA_SVC_BASE + 0x20u) != 0u; i++) (void)W(AE350_LOOPBACK_DATA);
    ae350_dma_reset();
    W(HS_DST) = 0;
    __asm__ volatile("fence" ::: "memory");
    bind(&req);
    req.need_cap = AE350_CAP_DMA_REQ;
    req.src = AE350_LOOPBACK_DATA;
    req.dst = HS_DST;
    req.bytes = 4u;
    req.src_mode = 2u;
    req.handshake = 2u;
    if (ae350_dma_submit(&req) != AE350_DMA_OK) errors |= 512u;
    W(AE350_DMA_SVC_BASE + 0x0cu) = 2u;
    uint32_t stayed = 1u;
    for (uint32_t i = 0; i < 400u; i++) if (ae350_dma_busy(0) == 0u) stayed = 0u;
    W(AE350_LOOPBACK_DATA) = 0x74000002u;
    if (!stayed || ae350_dma_wait(0) != AE350_DMA_OK || W(HS_DST) != 0x74000002u) errors |= 512u;
    slot[HS_RX] = W(HS_DST);
    W(AE350_DMA_SVC_BASE + 0x0cu) = 1u;
    slot[HS_ERRORS] = errors;
}

void main(void) {
    for (uint32_t i = 0; i < 32u; i++) slot[i] = 0;
    slot[SCHEMA] = 0x50330002u;
    slot[STAGE] = 0xC3100001u;
    dc_off();
    inval_all();
    ae350_dma_reset();
    rejections();
    bulk();
    chains();
    widths();
    ownership();
    handshake();
    slot[DMAC_CFG] = W(AE350_DMAC_BASE + 0x10u);
    slot[STAGE] = 0xC3100004u;
    for (;;) __asm__ volatile("wfi");
}
