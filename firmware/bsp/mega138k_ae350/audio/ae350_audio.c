#include "ae350_audio.h"
#include "ae350_address_map.h"
#define W(a) (*(volatile uint32_t *)(uintptr_t)(a))
#define TX(o) W(AE350_AUDIO_BASE+(o))
#define RX(o) W(AE350_MIC_BASE+(o))
struct transfer { unsigned active, channel; uint32_t buffer, bytes; };
static struct transfer tx, rx;
static unsigned tx_bound, rx_bound;

static int bind(uint32_t base, uint32_t id, uint32_t data, uint32_t req, unsigned entry) {
    uint32_t table = AE350_SYSINFO_BASE + 0x40u + entry * 32u;
    if (W(AE350_SYSINFO_BASE) != AE350_SYSINFO_ID || W(table) != id ||
        W(table+4) != base || W(table+8) != 0x1000u || W(table+12) != data ||
        W(table+16) != 4u || W(table+20) != AE350_ABI_VERSION ||
        ((W(table+24) >> 8) & 0xffu) != req || ((W(table+24) >> 16) & 7u) != 7u ||
        W(table+28) != (id==AE350_AUDIO_ID ? AE350_AUDIO_DMA_SEL : AE350_MIC_DMA_SEL))
        return AE350_DMA_ERR_BIND;
    if (W(base) != id || W(base+4) != AE350_ABI_VERSION || W(base+8) != 7u)
        return AE350_DMA_ERR_DEVICE;
    return AE350_DMA_OK;
}

static int buffer(uint32_t addr, unsigned count) {
    if (!count || count > 0x3fffffffu) return AE350_DMA_ERR_SIZE;
    if ((addr & 31u) || ((count * 4u) & 31u)) return AE350_DMA_ERR_ALIGN;
    if (addr >= AE350_DDR_SIZE || count * 4u > AE350_DDR_SIZE - addr) return AE350_DMA_ERR_REACH;
    return 0;
}

static int submit(struct transfer *t, unsigned channel, uint32_t addr, unsigned count, unsigned capture) {
    if (t->active) return AE350_DMA_ERR_BUSY;
    int rc = buffer(addr, count);
    if (rc) return rc;
    /* RX clean first so later invalidate cannot lose neighboring dirty bytes;
       complete cache lines are exclusively owned throughout the transfer. */
    rc = ae350_dma_sync_for_device(addr, count * 4u);
    if (rc) return rc;
    struct ae350_dma_req r = {
        .device=AE350_DMAC_ID, .abi=AE350_ABI_VERSION, .csr_base=AE350_DMAC_BASE,
        .irq_id=AE350_IRQ_ID, .need_cap=AE350_CAP_DMA_REQ, .channel=channel,
        .src=capture ? AE350_MIC_DATA : addr, .dst=capture ? addr : AE350_AUDIO_DATA,
        .bytes=count*4u, .width_bytes=4, .src_mode=capture ? 2u : 0u,
        .dst_mode=capture ? 0u : 2u, .handshake=capture ? 4u : 3u, .llp=0
    };
    rc = ae350_dma_submit(&r);
    if (rc) return rc;
    *t = (struct transfer){1, channel, addr, count*4u};
    return 0;
}

static int done(struct transfer *t, unsigned capture) {
    if (!t->active) return AE350_DMA_ERR_INCOMPLETE;
    int rc = ae350_dma_poll(t->channel);
    if (rc == AE350_DMA_ERR_BUSY) return rc;
    /* Enabled=0 after completion/error: all transfers are drained. Keep a
       failed buffer owned until stop, so the caller cannot mistake error for
       usable capture data. */
    if (rc) return rc;
    if (capture) {
        rc = ae350_dma_sync_for_cpu(t->buffer, t->bytes);
        if (rc) return rc;
    }
    t->active = 0;
    return 0;
}

uint32_t audio_tx_info(unsigned offset) { return tx_bound && offset<=0x44u && !(offset&3u) ? TX(offset) : 0; }
uint32_t mic_rx_info(unsigned offset) { return rx_bound && offset<=0x48u && !(offset&3u) ? RX(offset) : 0; }

int audio_tx_stop(void) {
    if (!tx_bound) return AE350_DMA_ERR_BIND;
    TX(0x0c) = 2; /* mute/pause and withdraw requests before aborting */
    __asm__ volatile("fence" ::: "memory");
    if (tx.active) {
        int rc=ae350_dma_abort_drain(tx.channel);
        if (rc) return rc;
        tx.active=0;
    }
    TX(0x0c)=10; /* clear only after in-flight bus accesses finish */
    return 0;
}
int audio_tx_init(void) {
    int rc=bind(AE350_AUDIO_BASE, AE350_AUDIO_ID, AE350_AUDIO_DATA, AE350_AUDIO_REQ, 5);
    if (rc) return rc;
    tx_bound=1;
    return audio_tx_stop();
}
int audio_tx_write(const uint32_t *pcm, unsigned count, unsigned *written) {
    if (!tx_bound || !written || (!pcm && count)) return AE350_DMA_ERR_BIND;
    *written=0;
    if (tx.active || (TX(0x0c)&4u)) return AE350_DMA_ERR_BUSY;
    while (*written<count && TX(AUDIO_LEVEL)<TX(AUDIO_DEPTH)) {
        W(AE350_AUDIO_DATA)=pcm[*written]; (*written)++;
    }
    return *written==count ? 0 : AE350_DMA_ERR_BUSY;
}
int audio_tx_start(unsigned mute) {
    if (!tx_bound) return AE350_DMA_ERR_BIND;
    TX(0x0c)=(TX(0x0c)&4u)|1u|((mute!=0u)<<1); return 0;
}
int audio_tx_pause(void) {
    if (!tx_bound) return AE350_DMA_ERR_BIND;
    TX(0x0c)=(TX(0x0c)&4u)|2u; return 0;
}
int audio_tx_mute(unsigned mute) {
    if (!tx_bound) return AE350_DMA_ERR_BIND;
    TX(0x0c)=(TX(0x0c)&5u)|((mute!=0u)<<1); return 0;
}
int audio_tx_dma_start(unsigned channel, const uint32_t *pcm, unsigned count) {
    if (!tx_bound) return AE350_DMA_ERR_BIND;
    int rc=submit(&tx, channel, (uint32_t)(uintptr_t)pcm, count, 0);
    if (!rc) TX(0x0c)=TX(0x0c)|4u;
    return rc;
}
int audio_tx_dma_done(void) { return done(&tx,0); }

int mic_rx_stop(void) {
    if (!rx_bound) return AE350_DMA_ERR_BIND;
    /* Keep clocks/format until the last outstanding read has drained. */
    RX(0x0c)=RX(0x0c)&7u;
    __asm__ volatile("fence" ::: "memory");
    if (rx.active) {
        int rc=ae350_dma_abort_drain(rx.channel);
        if (rc) return rc;
        rx.active=0;
    }
    RX(0x0c)=32;
    return 0;
}
int mic_rx_init(unsigned right) {
    int rc=bind(AE350_MIC_BASE,AE350_MIC_ID,AE350_MIC_DATA,AE350_MIC_REQ,6);
    if (rc) return rc;
    rx_bound=1;
    rc=mic_rx_stop(); if (rc) return rc;
    RX(0x0c)=1u|((right!=0u)<<1);
    return 0;
}
int mic_rx_capture(void) {
    if (!rx_bound) return AE350_DMA_ERR_BIND;
    if (!(RX(0x0c)&1u)) return AE350_DMA_ERR_BIND;
    if (rx.active || (RX(0x10)&1u)) return AE350_DMA_ERR_BUSY;
    RX(0x0c)=(RX(0x0c)&3u)|16u; return 0;
}
int mic_rx_stream(void) {
    if (!rx_bound) return AE350_DMA_ERR_BIND;
    if (!(RX(0x0c)&1u)) return AE350_DMA_ERR_BIND;
    if (rx.active || (RX(0x10)&1u)) return AE350_DMA_ERR_BUSY;
    RX(0x0c)=(RX(0x0c)&3u)|4u|16u; return 0;
}
int mic_rx_read(int32_t *samples, unsigned count, unsigned *read) {
    if (!rx_bound || !read || (!samples&&count)) return AE350_DMA_ERR_BIND;
    *read=0;
    if (rx.active || (RX(0x0c)&8u)) return AE350_DMA_ERR_BUSY;
    while (*read<count && RX(AUDIO_LEVEL)) samples[(*read)++]=(int32_t)W(AE350_MIC_DATA);
    return *read==count ? 0 : AE350_DMA_ERR_BUSY;
}
int mic_rx_dma_start(unsigned channel, int32_t *samples, unsigned count) {
    if (!rx_bound) return AE350_DMA_ERR_BIND;
    if (!(RX(0x0c)&1u)) return AE350_DMA_ERR_BIND;
    int rc=submit(&rx,channel,(uint32_t)(uintptr_t)samples,count,1);
    if (!rc) RX(0x0c)=RX(0x0c)|8u;
    return rc;
}
int mic_rx_dma_done(void) { return done(&rx,1); }
