#include <hal/hal.h>
#include <generated/csr.h>
#include <generated/soc.h>
static uint32_t *dma_ring;
static unsigned capacity,producer,index,control;
hal_result_t hal_audio_stop(void) {
    audio_control_write(0);audio_clear_write(1);control=0;
    uint32_t start=hal_time_ms();
    while(audio_busy_read())if((uint32_t)(hal_time_ms()-start)>=100)return HAL_TIMEOUT;
    audio_clear_write(1);audio_producer_write(0);
    dma_ring=0;capacity=producer=index=0;return HAL_OK;
}
hal_result_t hal_audio_start(void) {
    if(audio_errors_read())return HAL_IO;
    control|=1;audio_control_write(control);return HAL_OK;
}
void hal_audio_pause(void) {control&=~1u;audio_control_write(control);}
void hal_audio_mute(unsigned muted) {control=(control&~4u)|(muted?4u:0);audio_control_write(control);}
hal_result_t hal_audio_write(const uint32_t *pcm,unsigned frames,unsigned *written) {
    if(written)*written=0;
    if((!pcm && frames) || dma_ring)return HAL_INVALID;
    unsigned n=0;
    while(n<frames && audio_level_read()<AUDIO_FIFO_FRAMES)audio_sample_write(pcm[n++]);
    if(written)*written=n;
    return n==frames?HAL_OK:HAL_BUSY;
}
hal_result_t hal_audio_ring_begin(uint32_t *ring,unsigned frames) {
    uintptr_t base=(uintptr_t)ring;
    if(!frames || frames>65535 || (base&3u) || base<0x40000000u || base>=0x47e00000u
        || frames>(0x47e00000u-base)/4u)return HAL_INVALID;
    hal_result_t result=hal_audio_stop();if(result!=HAL_OK)return result;
    dma_ring=ring;capacity=frames;
    audio_base_write(base);audio_capacity_write(frames);audio_producer_write(0);
    control=2|4;audio_control_write(control);return HAL_OK;
}
hal_result_t hal_audio_ring_write(const uint32_t *pcm,unsigned frames,unsigned *written) {
    if(written)*written=0;
    if(!dma_ring || (!pcm && frames))return HAL_INVALID;
    if(audio_errors_read())return HAL_IO;
    unsigned used=producer-audio_fetched_read();if(used>capacity)return HAL_IO;
    unsigned n=frames<capacity-used?frames:capacity-used;
    for(unsigned i=0;i<n;++i) {
        dma_ring[index++]=pcm[i];if(index==capacity)index=0;
    }
    __asm__ volatile("fence rw,rw":::"memory");
    producer+=n;audio_producer_write(producer);
    if(written)*written=n;
    return n==frames?HAL_OK:HAL_BUSY;
}
void hal_audio_get_info(hal_audio_info_t *info) {
    if(info)*info=(hal_audio_info_t){AUDIO_SAMPLE_RATE,audio_control_read(),audio_level_read(),
        audio_frames_read(),audio_played_read(),audio_underruns_read(),audio_overruns_read(),
        audio_fetched_read(),audio_wraps_read(),audio_errors_read(),audio_busy_read(),
        audio_amplifier_read(),audio_last_sample_read()};
}
