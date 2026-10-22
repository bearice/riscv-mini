#pragma once
#include <stdint.h>
#include "ae350_dma.h"

/* Single caller per device; channels are supplied by the caller and exclusively
 * owned until done/stop succeeds. PCM16 stereo words: low16=L, high16=R.
 * RX words are signed PCM24 sign-extended to int32_t. DMA buffers must own
 * complete 32-byte cache lines and stay alive until done/stop succeeds. */
int audio_tx_init(void);
int audio_tx_write(const uint32_t *pcm, unsigned frames, unsigned *written);
int audio_tx_start(unsigned mute);
int audio_tx_pause(void);
int audio_tx_mute(unsigned mute);
int audio_tx_stop(void);
int audio_tx_dma_start(unsigned channel, const uint32_t *pcm, unsigned frames);
int audio_tx_dma_done(void);
int mic_rx_init(unsigned right);
/* Allow at least 9600 clocked frames (200ms) after init before first arm. */
int mic_rx_capture(void);
int mic_rx_stream(void);
int mic_rx_read(int32_t *samples, unsigned count, unsigned *read);
int mic_rx_dma_start(unsigned channel, int32_t *samples, unsigned count);
int mic_rx_dma_done(void);
int mic_rx_stop(void);

/* Offsets shared by both device pages. Status/counters are observations,
 * reads have no FIFO side effect. FIFO access uses AHB DATA only. */
enum { AUDIO_RATE=0x20, AUDIO_LEVEL=0x24, AUDIO_DEPTH=0x28,
       AUDIO_FRAMES=0x2c, AUDIO_PLAYED=0x30, AUDIO_UNDERRUN=0x34,
       AUDIO_LAST=0x3c, MIC_SAMPLES=0x2c, MIC_CAPTURED=0x30,
       MIC_OVERRUN=0x34, MIC_LAST=0x38, MIC_ACCEPTED=0x44 };
uint32_t audio_tx_info(unsigned offset);
uint32_t mic_rx_info(unsigned offset);
