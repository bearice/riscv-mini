#include <hal/hal.h>
#include <generated/csr.h>
#include <generated/soc.h>

hal_result_t hal_mic_start(unsigned right) {
    if(right>1)return HAL_INVALID;
    mic_control_write(right<<1);mic_clear_write(1);
    mic_control_write(1|(right<<1));return HAL_OK;
}
void hal_mic_stop(void) {mic_control_write(0);mic_clear_write(1);}
hal_result_t hal_mic_start_stereo(void) {
#if MINI_FEATURE_MIC_STEREO
    mic_control_write(0);mic_clear_write(1);mic_control_write(5);return HAL_OK;
#else
    return HAL_UNSUPPORTED;
#endif
}
hal_result_t hal_mic_capture(void) {
    if(!(mic_control_read()&1))return HAL_INVALID;
    if(mic_busy_read() || mic_level_read())return HAL_BUSY;
    mic_capture_write(1);return HAL_OK;
}
hal_result_t hal_mic_read(int32_t *samples,unsigned capacity,unsigned *read) {
    if(read)*read=0;
    if(!samples || !read || !capacity)return HAL_INVALID;
    if(mic_control_read()&4)return HAL_INVALID;
    if(!mic_done_read())return HAL_BUSY;
    if(mic_overruns_read())return HAL_IO;
    unsigned n=0;
    while(n<capacity && mic_level_read()) {
        samples[n++]=(int32_t)mic_sample_read();mic_pop_write(1);
        if(!(n&31))hal_poll();
    }
    *read=n;return HAL_OK;
}
hal_result_t hal_mic_read_stereo(hal_mic_pair_t *samples,unsigned capacity,unsigned *read) {
    if(read)*read=0;
    if(!MINI_FEATURE_MIC_STEREO)return HAL_UNSUPPORTED;
    if(!samples || !read || !capacity || !(mic_control_read()&4))return HAL_INVALID;
    if(!mic_done_read())return HAL_BUSY;
    if(mic_overruns_read())return HAL_IO;
    unsigned n=0;
    while(n<capacity && mic_level_read()) {
        samples[n].left=(int32_t)mic_sample_read();samples[n].right=(int32_t)mic_sample_right_read();
        ++n;mic_pop_write(1);if(!(n&31))hal_poll();
    }
    *read=n;return HAL_OK;
}
void hal_mic_get_info(hal_mic_info_t *info) {
    if(info)*info=(hal_mic_info_t){MIC_SAMPLE_RATE,mic_control_read(),mic_samples_read(),mic_last_sample_read(),
        mic_level_read(),mic_busy_read(),mic_done_read(),mic_captured_read(),mic_overruns_read(),mic_activity_read(),
        mic_last_right_read(),mic_activity_right_read()};
}
