#include "ae350_dma.h"
#include "ae350_address_map.h"

#define DMAC(off) (*(volatile uint32_t *)(uintptr_t)(AE350_DMAC_BASE + (off)))
#define W(addr) (*(volatile uint32_t *)(uintptr_t)(addr))

__attribute__((section(".ilm.text"), noinline))
static int in_ddr(uint32_t addr, uint32_t bytes) {
    if (bytes == 0 || addr >= AE350_DDR_SIZE || bytes > AE350_DDR_SIZE - addr) return 0;
    return 1;
}

__attribute__((section(".ilm.text"), noinline))
static int endpoint(uint32_t addr, uint32_t width, uint32_t handshake, uint32_t source) {
    if (handshake && ((source && !(handshake & 1u)) || (!source && (handshake & 1u))))
        return width == 4u && addr == (handshake <= 2u ? AE350_LOOPBACK_DATA :
                                     (handshake <= 4u ? (source ? AE350_MIC_DATA : AE350_AUDIO_DATA) :
                                      (source ? AE350_SD_RX_DATA : AE350_SD_TX_DATA)));
    return in_ddr(addr, width);
}

__attribute__((section(".ilm.text"), noinline))
static int span(uint32_t addr, uint32_t bytes, uint32_t width, uint32_t mode,
                uint32_t handshake, uint32_t source) {
    uint32_t count;
    uint32_t first;
    if (width == 0 || (bytes % width) != 0) return AE350_DMA_ERR_ALIGN;
    count = bytes / width;
    if (count == 0) return AE350_DMA_ERR_SIZE;
    if (mode == 2u) {
        if ((addr & (width - 1u)) != 0 || !endpoint(addr, width, handshake, source))
            return AE350_DMA_ERR_REACH;
        return AE350_DMA_OK;
    }
    if (mode == 1u) {
        if (count > 1u && addr < (count - 1u) * width) return AE350_DMA_ERR_REACH;
        first = addr - (count - 1u) * width;
    } else if (mode == 0u) {
        first = addr;
    } else {
        return AE350_DMA_ERR_ALIGN;
    }
    if ((first & (width - 1u)) != 0 || (addr & (width - 1u)) != 0) return AE350_DMA_ERR_ALIGN;
    if (!in_ddr(first, bytes)) return AE350_DMA_ERR_REACH;
    return AE350_DMA_OK;
}

__attribute__((section(".ilm.text"), noinline))
static uint32_t width_code(uint32_t width) {
    if (width == 1u) return 0;
    if (width == 2u) return 1;
    if (width == 4u) return 2;
    return 0xffu;
}

__attribute__((section(".ilm.text"), noinline, aligned(4)))
uint32_t ae350_dma_busy(uint32_t channel) {
    if (channel > 7u) return 0;
    return (DMAC(0x34u) >> channel) & 1u;
}

__attribute__((section(".ilm.text"), noinline, aligned(4)))
void ae350_dma_reset(void) {
    DMAC(0x20) = 1u;
    DMAC(0x30) = 0x00ffffffu;
}

__attribute__((section(".ilm.text"), noinline, aligned(4)))
int ae350_dma_abort_drain(uint32_t channel) {
    uint32_t bit;
    uint32_t ctrl;
    if (channel > 7u) return AE350_DMA_ERR_BIND;
    bit = 1u << channel;
    ctrl = 0x40u + channel * 0x20u;
    uint32_t enabled = (DMAC(0x34u) & bit) != 0;
    if (enabled) {
        DMAC(0x24) = bit;
    }
    /* Abort plus disable, then observe the global activity word. */
    DMAC(ctrl) = DMAC(ctrl) & ~1u;
    __asm__ volatile("fence" ::: "memory");
    for (uint32_t i = 0; i < 20000u; i++) {
        uint32_t status = DMAC(0x30);
        uint32_t ended = (status & (bit << 16)) && DMAC(ctrl+4u)==0u && DMAC(ctrl+0x18u)==0u;
        if ((DMAC(0x34u) & bit) == 0u && (!enabled || (status & (bit | (bit << 8))) || ended)) {
            __asm__ volatile("fence" ::: "memory");
            DMAC(0x30) = (bit << 16) | (bit << 8) | bit;
            return AE350_DMA_OK;
        }
    }
    return AE350_DMA_ERR_DRAIN;
}

__attribute__((section(".ilm.text"), noinline, aligned(4)))
int ae350_dma_poll(uint32_t channel) {
    uint32_t status, bit, busy;
    if (channel > 7u) return AE350_DMA_ERR_BIND;
    bit = 1u << channel;
    /* Read activity BEFORE terminal status. Reading status first can race a
       completion between the two reads and invent an INCOMPLETE result. */
    busy = ae350_dma_busy(channel);
    __asm__ volatile("fence" ::: "memory");
    /* MUG1029-1.3E table14-8: Error[7:0], Abort[15:8], TC[23:16].
       Errors take precedence even if TC happens to be set as well. */
    status = DMAC(0x30);
    if (status & bit) return AE350_DMA_ERR_HW;
    if (status & (bit << 8)) return AE350_DMA_ERR_ABORT;
    if (busy) return AE350_DMA_ERR_BUSY;
    __asm__ volatile("fence" ::: "memory");
    return (status & (bit << 16)) ? AE350_DMA_OK : AE350_DMA_ERR_INCOMPLETE;
}

__attribute__((section(".ilm.text"), noinline, aligned(4)))
int ae350_dma_wait(uint32_t channel) {
    int drained;
    if (channel > 7u) return AE350_DMA_ERR_BIND;
    for (uint32_t i = 0; i < 20000u; i++) {
        int result = ae350_dma_poll(channel);
        if (result != AE350_DMA_ERR_BUSY) return result;
    }
    drained = ae350_dma_abort_drain(channel);
    if (drained == AE350_DMA_OK) return AE350_DMA_ERR_TIMEOUT;
    return drained;
}

__attribute__((section(".ilm.text"), noinline))
static int sync_range(uint32_t addr, uint32_t bytes, uint32_t command) {
    uint32_t last;
    if (bytes == 0) return AE350_DMA_ERR_SIZE;
    if (!in_ddr(addr, bytes)) return AE350_DMA_ERR_REACH;
    last = (addr + bytes - 1u) & ~(AE350_DMA_LINE - 1u);
    for (uint32_t line = addr & ~(AE350_DMA_LINE - 1u); line <= last; line += AE350_DMA_LINE) {
        __asm__ volatile("csrw 0x7cb, %0" :: "r"(line) : "memory");
        __asm__ volatile("csrw 0x7cc, %0" :: "r"(command) : "memory");
        if (line == last) break;
    }
    __asm__ volatile("fence" ::: "memory");
    return AE350_DMA_OK;
}

__attribute__((section(".ilm.text"), noinline, aligned(4)))
int ae350_dma_sync_for_device(uint32_t addr, uint32_t bytes) {
    return sync_range(addr, bytes, 1u);
}

__attribute__((section(".ilm.text"), noinline, aligned(4)))
int ae350_dma_sync_for_cpu(uint32_t addr, uint32_t bytes) {
    return sync_range(addr, bytes, 0u);
}

__attribute__((section(".ilm.text"), noinline, aligned(4)))
int ae350_dma_submit(const struct ae350_dma_req *req) {
    uint32_t code;
    uint32_t base;
    uint32_t ctrl;
    int src;
    int dst;
    if (req == 0) return AE350_DMA_ERR_BIND;
    if (req->abi != W(AE350_SYSINFO_BASE + 0x04u)) return AE350_DMA_ERR_ABI;
    if (W(AE350_SYSINFO_BASE) != AE350_SYSINFO_ID) return AE350_DMA_ERR_BIND;
    if (req->device != W(AE350_SYSINFO_BASE + 0x60u)) return AE350_DMA_ERR_DEVICE;
    if (req->csr_base != W(AE350_SYSINFO_BASE + 0x64u)) return AE350_DMA_ERR_BIND;
    if (req->irq_id != W(AE350_IRQ_BASE)) return AE350_DMA_ERR_BIND;
    if ((req->need_cap & ~W(AE350_SYSINFO_BASE + 0x08u)) != 0) return AE350_DMA_ERR_CAP;
    if (req->channel > 7u) return AE350_DMA_ERR_BIND;
    if (req->handshake > 6u) return AE350_DMA_ERR_BIND;
    if (ae350_dma_busy(req->channel)) return AE350_DMA_ERR_BUSY;
    if (req->handshake && (((req->handshake & 1u) && req->dst_mode != 2u) ||
                           (!(req->handshake & 1u) && req->src_mode != 2u))) return AE350_DMA_ERR_BIND;
    code = width_code(req->width_bytes);
    if (code == 0xffu) return AE350_DMA_ERR_WIDTH;
    if (req->bytes == 0) return AE350_DMA_ERR_SIZE;
    src = span(req->src, req->bytes, req->width_bytes, req->src_mode, req->handshake, 1u);
    if (src != AE350_DMA_OK) return src;
    dst = span(req->dst, req->bytes, req->width_bytes, req->dst_mode, req->handshake, 0u);
    if (dst != AE350_DMA_OK) return dst;
    if (req->llp != 0u) {
        if ((req->llp & 7u) != 0 || !in_ddr(req->llp, 32u)) return AE350_DMA_ERR_ALIGN;
    }
    /* Bits 3 and 1 are interrupt masks: 1 disables the output (MUG1029). */
    ctrl = (code << 21) | (code << 18) | (req->src_mode << 14) | (req->dst_mode << 12) |
           (1u << 3) | (1u << 2) | (1u << 1);
    if (req->handshake & 1u)
        ctrl |= (1u << 16) | ((req->handshake == 1u ? 0u : (req->handshake == 3u ? AE350_AUDIO_DMA_SEL : AE350_SD_TX_DMA_SEL)) << 4);
    else if (req->handshake)
        ctrl |= (1u << 17) | ((req->handshake == 2u ? 1u : (req->handshake == 4u ? AE350_MIC_DMA_SEL : AE350_SD_RX_DMA_SEL)) << 8);
    base = 0x40u + req->channel * 0x20u;
    DMAC(0x30) = (1u << req->channel) * 0x10101u;
    DMAC(base) = 0;
    DMAC(base + 0x04u) = req->bytes / req->width_bytes;
    DMAC(base + 0x08u) = req->src;
    DMAC(base + 0x0cu) = 0;
    DMAC(base + 0x10u) = req->dst;
    DMAC(base + 0x14u) = 0;
    DMAC(base + 0x18u) = req->llp;
    DMAC(base + 0x1cu) = 0;
    __asm__ volatile("fence" ::: "memory");
    DMAC(base) = ctrl | 1u;
    __asm__ volatile("fence" ::: "memory");
    return AE350_DMA_OK;
}
