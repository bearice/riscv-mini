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
void hal_leds_set(unsigned mask); /* bit0..5 = schematic Orange_LED[0..5]; 1 is on */
unsigned hal_leds_get(void);
unsigned hal_buttons_read(void); /* bit0..3 = Key2..5; 1 is pressed */
unsigned hal_switches_read(void); /* bit0..3 = E9,E8,T4,T5; 1 is ON */
void hal_buttons_take(unsigned *pressed,unsigned *released); /* coalesced transition masks */
hal_result_t hal_ws2812_set(uint8_t red,uint8_t green,uint8_t blue);
unsigned hal_ws2812_busy(void);
hal_result_t hal_phys_reset(unsigned hold_ms); /* F10 resets BOTH Ethernet/USB PHYs */
hal_result_t hal_spi_transfer(hal_spi_bus_t bus,unsigned value,unsigned bits,unsigned *received);
hal_result_t hal_spi_select(hal_spi_bus_t bus,unsigned selected);
typedef struct {uint32_t sectors,clock_hz,bus_width,native,present,initialized,read_blocks,written_blocks,errors;} hal_sd_info_t;
void hal_sd_get_info(hal_sd_info_t *info);
hal_result_t hal_sd_mount(void);
hal_result_t hal_sd_list(void);
hal_result_t hal_sd_read(uint32_t sector,void *data,unsigned count);
hal_result_t hal_sd_write(uint32_t sector,const void *data,unsigned count);
hal_result_t hal_flash_probe(uint32_t *id,unsigned *bytes);
hal_result_t hal_flash_read(unsigned address,void *data,unsigned length);
hal_result_t hal_flash_program(unsigned address,const void *data,unsigned length);
hal_result_t hal_flash_erase(unsigned address);
hal_result_t hal_spi_lcd_show(unsigned sd_ready);
hal_result_t hal_video_init(void);
volatile uint16_t *hal_video_frame(unsigned slot);
hal_result_t hal_video_present(unsigned slot);
hal_result_t hal_video_stop(void);
void hal_video_status(void);
void hal_reboot(void) __attribute__((noreturn));
#ifdef __cplusplus
}
#endif
