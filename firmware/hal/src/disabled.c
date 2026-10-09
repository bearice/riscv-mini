/* Disabled modules: explicit UNSUPPORTED, empty queues and zero status. */
#include <hal/hal.h>
#include <string.h>
#if !MINI_FEATURE_SD
hal_result_t hal_sd_init(void) {return HAL_UNSUPPORTED;}
#endif
#if !MINI_FEATURE_ETH
hal_result_t hal_eth_init(void) {return HAL_UNSUPPORTED;}
#endif
#if !MINI_FEATURE_ETH
void hal_eth_stop(void) {}
#endif
#if !MINI_FEATURE_ETH
void hal_eth_poll(void) {}
#endif
#if !MINI_FEATURE_ETH
void hal_eth_get_info(hal_eth_info_t *info) {if(info)memset(info,0,sizeof(*info));}
#endif
#if !MINI_FEATURE_ETH
hal_result_t hal_eth_get_mac(uint8_t mac[6]) {(void)mac;return HAL_UNSUPPORTED;}
#endif
#if !MINI_FEATURE_ETH
hal_result_t hal_eth_mdio_read(unsigned address,unsigned reg,uint16_t *value) {(void)address;(void)reg;(void)value;return HAL_UNSUPPORTED;}
#endif
#if !MINI_FEATURE_ETH
hal_result_t hal_eth_mdio_write(unsigned address,unsigned reg,uint16_t value) {(void)address;(void)reg;(void)value;return HAL_UNSUPPORTED;}
#endif
#if !MINI_FEATURE_ETH
hal_result_t hal_eth_send(const void *frame,unsigned length) {(void)frame;(void)length;return HAL_UNSUPPORTED;}
#endif
#if !MINI_FEATURE_ETH
hal_result_t hal_eth_receive(void *frame,unsigned capacity,unsigned *length) {(void)frame;(void)capacity;(void)length;if(length)*length=0;return HAL_UNSUPPORTED;}
#endif
#if !MINI_FEATURE_USB
hal_result_t hal_usb_init(void) {return HAL_UNSUPPORTED;}
#endif
#if !MINI_FEATURE_USB
void hal_usb_stop(void) {}
#endif
#if !MINI_FEATURE_USB
void hal_usb_poll(void) {}
#endif
#if !MINI_FEATURE_USB
void hal_usb_get_info(hal_usb_info_t *info) {if(info)memset(info,0,sizeof(*info));}
#endif
#if !MINI_FEATURE_USB
hal_result_t hal_usb_device_info(uint8_t device,hal_usb_device_t *info) {(void)device;if(info)memset(info,0,sizeof(*info));return HAL_UNSUPPORTED;}
#endif
#if !MINI_FEATURE_USB
hal_result_t hal_usb_key_take(hal_usb_key_t *key) {(void)key;return HAL_UNSUPPORTED;}
#endif
#if !MINI_FEATURE_USB
hal_result_t hal_usb_report_take(hal_usb_report_t *report) {(void)report;return HAL_UNSUPPORTED;}
#endif
#if !MINI_FEATURE_USB
hal_result_t hal_usb_mouse_take(hal_usb_mouse_t *mouse) {(void)mouse;return HAL_UNSUPPORTED;}
#endif
#if !MINI_FEATURE_USB
hal_result_t hal_usb_keyboard_leds(uint8_t device,uint8_t interface,uint8_t leds) {(void)device;(void)interface;(void)leds;return HAL_UNSUPPORTED;}
#endif
#if !MINI_FEATURE_SD
void hal_sd_get_info(hal_sd_info_t *info) {if(info)memset(info,0,sizeof(*info));}
#endif
#if !MINI_FEATURE_FILESYSTEM
hal_result_t hal_sd_mount(void) {return HAL_UNSUPPORTED;}
#endif
#if !MINI_FEATURE_FILESYSTEM
hal_result_t hal_sd_list(void) {return HAL_UNSUPPORTED;}
#endif
#if !MINI_FEATURE_SD
hal_result_t hal_sd_read(uint32_t sector,void *data,unsigned count) {(void)sector;(void)data;(void)count;return HAL_UNSUPPORTED;}
#endif
#if !MINI_FEATURE_SD
hal_result_t hal_sd_write(uint32_t sector,const void *data,unsigned count) {(void)sector;(void)data;(void)count;return HAL_UNSUPPORTED;}
#endif
#if !MINI_FEATURE_FLASH
hal_result_t hal_flash_probe(uint32_t *id,unsigned *bytes) {(void)id;(void)bytes;if(bytes)*bytes=0;if(id)*id=0;return HAL_UNSUPPORTED;}
#endif
#if !MINI_FEATURE_FLASH
hal_result_t hal_flash_uid(uint8_t uid[16],unsigned *length) {(void)uid;(void)length;if(length)*length=0;return HAL_UNSUPPORTED;}
#endif
#if !MINI_FEATURE_FLASH
hal_result_t hal_flash_read(unsigned address,void *data,unsigned length) {(void)address;(void)data;(void)length;return HAL_UNSUPPORTED;}
#endif
#if !MINI_FEATURE_FLASH
hal_result_t hal_flash_program(unsigned address,const void *data,unsigned length) {(void)address;(void)data;(void)length;return HAL_UNSUPPORTED;}
#endif
#if !MINI_FEATURE_FLASH
hal_result_t hal_flash_erase(unsigned address) {(void)address;return HAL_UNSUPPORTED;}
#endif
#if !MINI_FEATURE_SPI_LCD
hal_result_t hal_spi_lcd_show(unsigned sd_ready) {(void)sd_ready;return HAL_UNSUPPORTED;}
#endif
#if !MINI_FEATURE_SPI_LCD
hal_result_t hal_spi_lcd_network(const uint8_t mac[6],const uint8_t ip[4]) {(void)mac;(void)ip;return HAL_UNSUPPORTED;}
#endif
#if !MINI_FEATURE_SPI_LCD
hal_result_t hal_spi_lcd_link(unsigned up) {(void)up;return HAL_UNSUPPORTED;}
#endif
#if !MINI_FEATURE_VIDEO
hal_result_t hal_video_init(void) {return HAL_UNSUPPORTED;}
hal_result_t hal_video_set_buffers(uint16_t *first,uint16_t *second) {(void)first;(void)second;return HAL_UNSUPPORTED;}
#endif
#if !MINI_FEATURE_VIDEO
volatile uint16_t * hal_video_frame(unsigned slot) {(void)slot;return 0;}
#endif
#if !MINI_FEATURE_VIDEO
hal_result_t hal_video_present(unsigned slot) {(void)slot;return HAL_UNSUPPORTED;}
#endif
#if !MINI_FEATURE_VIDEO
hal_result_t hal_video_stop(void) {return HAL_UNSUPPORTED;}
#endif
#if !MINI_FEATURE_VIDEO
void hal_video_status(void) {}
#endif
#if !MINI_FEATURE_MIC
hal_result_t hal_mic_start_stereo(void) {return HAL_UNSUPPORTED;}
#endif
#if !MINI_FEATURE_MIC
hal_result_t hal_mic_read_stereo(hal_mic_pair_t *samples,unsigned capacity,unsigned *read) {(void)samples;(void)capacity;(void)read;if(read)*read=0;return HAL_UNSUPPORTED;}
#endif
#if !MINI_FEATURE_MIC
hal_result_t hal_mic_start(unsigned right_channel) {(void)right_channel;return HAL_UNSUPPORTED;}
#endif
#if !MINI_FEATURE_MIC
void hal_mic_stop(void) {}
#endif
#if !MINI_FEATURE_MIC
hal_result_t hal_mic_capture(void) {return HAL_UNSUPPORTED;}
#endif
#if !MINI_FEATURE_MIC
hal_result_t hal_mic_read(int32_t *samples,unsigned capacity,unsigned *read) {(void)samples;(void)capacity;(void)read;if(read)*read=0;return HAL_UNSUPPORTED;}
#endif
#if !MINI_FEATURE_MIC
void hal_mic_get_info(hal_mic_info_t *info) {if(info)memset(info,0,sizeof(*info));}
#endif
#if !MINI_FEATURE_AUDIO
void hal_audio_get_info(hal_audio_info_t *info) {if(info)memset(info,0,sizeof(*info));}
#endif
#if !MINI_FEATURE_AUDIO
hal_result_t hal_audio_stop(void) {return HAL_UNSUPPORTED;}
#endif
#if !MINI_FEATURE_AUDIO
hal_result_t hal_audio_start(void) {return HAL_UNSUPPORTED;}
#endif
#if !MINI_FEATURE_AUDIO
void hal_audio_pause(void) {}
#endif
#if !MINI_FEATURE_AUDIO
void hal_audio_mute(unsigned muted) {(void)muted;}
#endif
#if !MINI_FEATURE_AUDIO
hal_result_t hal_audio_write(const uint32_t *pcm,unsigned frames,unsigned *written) {(void)pcm;(void)frames;(void)written;if(written)*written=0;return HAL_UNSUPPORTED;}
#endif
#if !MINI_FEATURE_AUDIO
hal_result_t hal_audio_ring_begin(uint32_t *ring,unsigned capacity_frames) {(void)ring;(void)capacity_frames;return HAL_UNSUPPORTED;}
#endif
#if !MINI_FEATURE_AUDIO
hal_result_t hal_audio_ring_write(const uint32_t *pcm,unsigned frames,unsigned *written) {(void)pcm;(void)frames;(void)written;if(written)*written=0;return HAL_UNSUPPORTED;}
#endif

#if !MINI_FEATURE_ETH
hal_result_t hal_eth_rx_acquire(const void **frame,unsigned *length) {(void)frame;(void)length;return HAL_UNSUPPORTED;}
hal_result_t hal_eth_rx_release(const void *frame) {(void)frame;return HAL_UNSUPPORTED;}
hal_result_t hal_eth_tx_acquire(void **frame,unsigned *capacity) {(void)frame;(void)capacity;return HAL_UNSUPPORTED;}
hal_result_t hal_eth_tx_commit(const void *frame,unsigned length) {(void)frame;(void)length;return HAL_UNSUPPORTED;}
#endif
