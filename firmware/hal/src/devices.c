#include <hal/hal.h>
#include "io.h"
#include "flash.h"
#include "ff.h"
#include "diskio.h"
#include <generated/soc.h>
extern void sd_get_info(hal_sd_info_t *info);
#if MINI_FEATURE_SD
void hal_sd_get_info(hal_sd_info_t *info) {if(info)sd_get_info(info);}
hal_result_t hal_sd_init(void) {return disk_initialize(0)?HAL_NO_MEDIA:HAL_OK;}
#endif
static __attribute__((unused)) hal_result_t result(int ok) {return ok?HAL_OK:HAL_IO;}
void hal_uart_puts(const char *text) {puts_uart(text);}
void hal_uart_putc(char ch) {putchar_uart(ch);}
void hal_uart_hex(uint32_t value) {io_hex(value);}
void hal_console_mirror(void (*callback)(char)) {io_console_mirror(callback);}
hal_result_t hal_spi_transfer(hal_spi_bus_t bus,unsigned value,unsigned bits,unsigned *received) {
    if((bus!=HAL_SPI_SD && bus!=HAL_SPI_LCD) || !bits || bits>(bus==HAL_SPI_SD?8u:16u))return HAL_INVALID;
    if((!MINI_FEATURE_SD || CONFIG_SD_NATIVE) && bus==HAL_SPI_SD)return HAL_UNSUPPORTED;
    if(!MINI_FEATURE_SPI_LCD && bus==HAL_SPI_LCD)return HAL_UNSUPPORTED;
#if MINI_FEATURE_SPI_LCD || (MINI_FEATURE_SD && !CONFIG_SD_NATIVE)
    return result(spi_transfer(bus,value,bits,received));
#else
    (void)value;(void)received;return HAL_UNSUPPORTED;
#endif
}
hal_result_t hal_spi_select(hal_spi_bus_t bus,unsigned selected) {
    if(bus!=HAL_SPI_SD && bus!=HAL_SPI_LCD)return HAL_INVALID;
    if((!MINI_FEATURE_SD || CONFIG_SD_NATIVE) && bus==HAL_SPI_SD)return HAL_UNSUPPORTED;
    if(!MINI_FEATURE_SPI_LCD && bus==HAL_SPI_LCD)return HAL_UNSUPPORTED;
#if MINI_FEATURE_SPI_LCD || (MINI_FEATURE_SD && !CONFIG_SD_NATIVE)
    spi_select(bus,!!selected);return HAL_OK;
#else
    (void)selected;return HAL_UNSUPPORTED;
#endif
}
#if MINI_FEATURE_FILESYSTEM
hal_result_t hal_sd_mount(void) {return sd_mount()?HAL_OK:HAL_NO_MEDIA;}
#endif
#if MINI_FEATURE_FILESYSTEM
hal_result_t hal_sd_list(void) {return result(sd_list());}
#endif
#if MINI_FEATURE_SD
static hal_result_t disk_result(DRESULT r) {return r==RES_OK?HAL_OK:r==RES_PARERR?HAL_INVALID:r==RES_NOTRDY?HAL_NO_MEDIA:HAL_IO;}
#endif
#if MINI_FEATURE_SD
hal_result_t hal_sd_read(uint32_t sector,void *data,unsigned count) {return data?disk_result(disk_read(0,data,sector,count)):HAL_INVALID;}
#endif
#if MINI_FEATURE_SD
hal_result_t hal_sd_write(uint32_t sector,const void *data,unsigned count) {return data?disk_result(disk_write(0,data,sector,count)):HAL_INVALID;}
#endif
#if MINI_FEATURE_FLASH
hal_result_t hal_flash_probe(uint32_t *id,unsigned *bytes) {int ok=flash_init(id);if(bytes)*bytes=flash_size();return result(ok);}
#endif
#if MINI_FEATURE_FLASH
hal_result_t hal_flash_uid(uint8_t uid[16],unsigned *length) {return uid && length?result(flash_uid(uid,length)):HAL_INVALID;}
#endif
#if MINI_FEATURE_FLASH
hal_result_t hal_flash_read(unsigned address,void *data,unsigned length) {return data?result(flash_read(address,data,length)):HAL_INVALID;}
#endif
#if MINI_FEATURE_FLASH
hal_result_t hal_flash_program(unsigned address,const void *data,unsigned length) {
    if(!data || address<MINI_FLASH_WRITABLE || address>=MINI_FLASH_SIZE || length>MINI_FLASH_SIZE-address)return HAL_INVALID;
    return result(flash_program(address,data,length));
}
#endif
#if MINI_FEATURE_FLASH
hal_result_t hal_flash_erase(unsigned address) {
    if((address&4095u) || address<MINI_FLASH_WRITABLE || address>MINI_FLASH_SIZE-4096)return HAL_INVALID;
    return result(flash_erase_sector(address));
}
#endif
#if MINI_FEATURE_SPI_LCD
hal_result_t hal_spi_lcd_show(unsigned sd_ready) {return result(lcd_show(sd_ready));}
#endif
#if MINI_FEATURE_SPI_LCD
hal_result_t hal_spi_lcd_network(const uint8_t mac[6],const uint8_t ip[4]) {return mac?result(lcd_network(mac,ip)):HAL_INVALID;}
#endif
#if MINI_FEATURE_SPI_LCD
hal_result_t hal_spi_lcd_link(unsigned up) {return result(lcd_link(!!up));}
#endif
#if MINI_FEATURE_VIDEO
hal_result_t hal_video_init(void) {return result(video_init());}
#endif
#if MINI_FEATURE_VIDEO
volatile uint16_t *hal_video_frame(unsigned slot) {return video_frame(slot);}
#endif
#if MINI_FEATURE_VIDEO
hal_result_t hal_video_present(unsigned slot) {return slot>1?HAL_INVALID:result(video_present(slot));}
#endif
#if MINI_FEATURE_VIDEO
hal_result_t hal_video_stop(void) {return result(video_stop());}
#endif
#if MINI_FEATURE_VIDEO
void hal_video_status(void) {video_status();}
#endif
