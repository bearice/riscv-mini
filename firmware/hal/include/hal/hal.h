#pragma once
#include <stdint.h>
#include <stddef.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef enum {HAL_OK=0,HAL_BUSY,HAL_TIMEOUT,HAL_IO,HAL_INVALID,HAL_NO_MEDIA,HAL_UNSUPPORTED} hal_result_t;
typedef enum {HAL_SPI_SD=0,HAL_SPI_LCD=1} hal_spi_bus_t;
typedef struct {uint32_t gpr[32],pc,status,cause,value;} hal_trap_frame_t;
typedef void (*hal_irq_handler_t)(void *context);
typedef struct {uint32_t timer_irqs,uart_irqs,button_irqs,uart_drops,unhandled_irqs;} hal_stats_t;
void hal_init(void);
void hal_poll(void);
uint32_t hal_time_ms(void);
uint32_t hal_ticks(void);
void hal_delay_ms(unsigned ms);
static inline int hal_deadline_reached(uint32_t now,uint32_t deadline) {return (int32_t)(now-deadline)>=0;}
uint32_t hal_irq_save(void);
void hal_irq_restore(uint32_t state);
hal_result_t hal_irq_attach(unsigned source,hal_irq_handler_t callback,void *context);
hal_result_t hal_irq_enable(unsigned source,unsigned enabled);
void hal_get_stats(hal_stats_t *stats);
/* Override to handle synchronous exceptions; advance frame->pc when resuming. */
void hal_exception_handler(hal_trap_frame_t *frame);
int hal_uart_getc(void); /* -1: no data */
hal_result_t hal_uart_write(const void *data,size_t size,unsigned timeout_ms,size_t *written);
void hal_uart_puts(const char *text);
void hal_uart_putc(char ch);
void hal_uart_hex(uint32_t value);
/* Mirror all driver/diagnostic text into a terminal; callback cannot emit UART. */
void hal_console_mirror(void (*callback)(char));
void hal_leds_set(unsigned mask); /* bit0..5 = schematic Orange_LED[0..5]; 1 is on */
unsigned hal_leds_get(void);
unsigned hal_buttons_read(void); /* bit0..3 = Key2..5; 1 is pressed */
unsigned hal_switches_read(void); /* bit0..3 = E9,E8,T4,T5; 1 is ON */
void hal_buttons_take(unsigned *pressed,unsigned *released); /* coalesced transition masks */
hal_result_t hal_ws2812_set(uint8_t red,uint8_t green,uint8_t blue);
unsigned hal_ws2812_busy(void);
/* Single main-loop owner; frames include Ethernet header, exclude preamble/FCS.
   No IP stack or MAC address filtering is imposed by the raw-frame HAL. */
#define HAL_ETH_MAX_FRAME 1514u
typedef struct {uint32_t initialized,phy_address,phy_id,link,speed_mbps,full_duplex,
    bmcr,bmsr,partner,rmii,ref_clock_hz,rx_frames,tx_frames,rx_drops,crc_errors,
    preamble_errors,irqs,mdio_errors,link_changes,tx_busy,uid_length,dma_rx,dma_tx;uint8_t mac[6],flash_uid[16];} hal_eth_info_t;
hal_result_t hal_eth_init(void); /* bounded setup; link may still be negotiating */
void hal_eth_stop(void);
void hal_eth_poll(void); /* also called by hal_poll; does not consume frames */
void hal_eth_get_info(hal_eth_info_t *info);
hal_result_t hal_eth_get_mac(uint8_t mac[6]); /* stable local unicast, seeded by factory Flash UID */
hal_result_t hal_eth_mdio_read(unsigned address,unsigned reg,uint16_t *value);
hal_result_t hal_eth_mdio_write(unsigned address,unsigned reg,uint16_t value);
hal_result_t hal_eth_send(const void *frame,unsigned length); /* BUSY while one TX is owned */
hal_result_t hal_eth_receive(void *frame,unsigned capacity,unsigned *length); /* BUSY: empty; INVALID: dropped oversized frame */
/* Native ring borrowing: at most one RX/TX borrow per caller. RX must be
 * released exactly once; TX commit transfers ownership until hardware completion.
 * Stop invalidates all outstanding borrows. PIO builds return UNSUPPORTED. */
hal_result_t hal_eth_rx_acquire(const void **frame,unsigned *length);
hal_result_t hal_eth_rx_release(const void *frame);
hal_result_t hal_eth_tx_acquire(void **frame,unsigned *capacity);
hal_result_t hal_eth_tx_commit(const void *frame,unsigned length);
hal_result_t hal_phys_reset(unsigned hold_ms); /* F10 resets BOTH Ethernet/USB PHYs */
typedef struct {uint32_t initialized,connected,phy_ready,phy_error,phy_id,lines,device,
    speed,hid_interfaces,mounts,unmounts,reports,key_events,key_drops,report_drops,rollovers,
    errors,irqs,control,port_status,frame,hcca,mouse_events,mouse_drops;uint16_t vid,pid;} hal_usb_info_t;
typedef struct {uint32_t time_ms;uint8_t device,interface,usage,pressed,modifiers;} hal_usb_key_t;
typedef struct {uint8_t device,interface,length,data[64];} hal_usb_report_t;
typedef struct {uint32_t time_ms;uint8_t device,interface,buttons;int8_t x,y,wheel;} hal_usb_mouse_t;
hal_result_t hal_usb_init(void); /* bounded PHY/controller setup; enumeration runs in hal_poll */
void hal_usb_stop(void);
void hal_usb_poll(void);
void hal_usb_get_info(hal_usb_info_t *info);
typedef struct {uint16_t vid,pid;uint8_t device,hub,port,speed,is_hub,hid_interfaces;} hal_usb_device_t;
/* Address scan: HAL_INVALID marks the end; HAL_NO_MEDIA is an unused slot. */
hal_result_t hal_usb_device_info(uint8_t device,hal_usb_device_t *info);
hal_result_t hal_usb_key_take(hal_usb_key_t *key); /* USB keyboard usages, including E0..E7 modifiers */
hal_result_t hal_usb_report_take(hal_usb_report_t *report); /* raw HID, single main-loop owner */
hal_result_t hal_usb_mouse_take(hal_usb_mouse_t *mouse); /* Boot mouse relative motion/buttons */
hal_result_t hal_usb_keyboard_leds(uint8_t device,uint8_t interface,uint8_t leds); /* async Num/Caps/Scroll/Compose/Kana */
hal_result_t hal_spi_transfer(hal_spi_bus_t bus,unsigned value,unsigned bits,unsigned *received);
hal_result_t hal_spi_select(hal_spi_bus_t bus,unsigned selected);
typedef struct {uint32_t sectors,clock_hz,bus_width,native,present,initialized,read_blocks,written_blocks,errors,
    read_clock_hz,write_clock_hz,high_speed;} hal_sd_info_t;
void hal_sd_get_info(hal_sd_info_t *info);
hal_result_t hal_sd_init(void); /* raw block access, does not require FatFs */
/* Native only, idle main-loop owner: 7.5/10/15/30 MHz. 30 requires CMD6 HS.
   Reinitialization restores the driver default; writes always use 7.5 MHz. */
hal_result_t hal_sd_set_read_clock(unsigned clock_hz);
hal_result_t hal_sd_mount(void);
hal_result_t hal_sd_list(void);
hal_result_t hal_sd_read(uint32_t sector,void *data,unsigned count);
hal_result_t hal_sd_write(uint32_t sector,const void *data,unsigned count);
hal_result_t hal_flash_probe(uint32_t *id,unsigned *bytes);
hal_result_t hal_flash_uid(uint8_t uid[16],unsigned *length);
hal_result_t hal_flash_read(unsigned address,void *data,unsigned length);
hal_result_t hal_flash_program(unsigned address,const void *data,unsigned length);
hal_result_t hal_flash_erase(unsigned address);
hal_result_t hal_spi_lcd_show(unsigned sd_ready);
hal_result_t hal_spi_lcd_network(const uint8_t mac[6],const uint8_t ip[4]); /* NULL IP: unconfigured */
hal_result_t hal_spi_lcd_link(unsigned up);
hal_result_t hal_video_init(void);
volatile uint16_t *hal_video_frame(unsigned slot);
hal_result_t hal_video_set_buffers(uint16_t *first,uint16_t *second);
hal_result_t hal_video_present(unsigned slot);
hal_result_t hal_video_stop(void);
void hal_video_status(void);
/* Standard I2S PCM24 snapshots; single main-loop owner. Independent of WS2812. */
typedef struct {uint32_t sample_rate,control,samples,last_sample,level,busy,done,captured,overruns,activity,last_right,activity_right;} hal_mic_info_t;
typedef struct {int32_t left,right;} hal_mic_pair_t;
hal_result_t hal_mic_start_stereo(void);
hal_result_t hal_mic_read_stereo(hal_mic_pair_t *samples,unsigned capacity,unsigned *read);
hal_result_t hal_mic_start(unsigned right_channel);
void hal_mic_stop(void);
hal_result_t hal_mic_capture(void);
hal_result_t hal_mic_read(int32_t *samples,unsigned capacity,unsigned *read);
void hal_mic_get_info(hal_mic_info_t *info);
/* Stereo PCM word: signed left in bits15:0, signed right in bits31:16.
   Single main-loop owner. DMA ring must remain allocated until stop succeeds. */
typedef struct {uint32_t sample_rate,control,level,frames,played,underruns,overruns,fetched,wraps,errors,busy,amplifier,last_sample;} hal_audio_info_t;
void hal_audio_get_info(hal_audio_info_t *info);
hal_result_t hal_audio_stop(void);
hal_result_t hal_audio_start(void);
void hal_audio_pause(void);
void hal_audio_mute(unsigned muted);
hal_result_t hal_audio_write(const uint32_t *pcm,unsigned frames,unsigned *written);
hal_result_t hal_audio_ring_begin(uint32_t *ring,unsigned capacity_frames);
hal_result_t hal_audio_ring_write(const uint32_t *pcm,unsigned frames,unsigned *written);
void hal_reboot(void) __attribute__((noreturn));
#ifdef __cplusplus
}
#endif
