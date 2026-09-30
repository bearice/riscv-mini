#pragma once
#include <stdint.h>
#include <generated/soc.h>
void puts_uart(const char *text);
void putchar_uart(char ch);
void io_timer_init(void);
uint32_t io_ticks(void);
void io_delay_ms(unsigned ms);
void io_hex(uint32_t value);
int spi_transfer(unsigned lcd, unsigned value, unsigned bits, unsigned *received);
void spi_select(unsigned lcd, unsigned selected);
int spi_selftest(void);
int lcd_show(unsigned sd_ready, unsigned write_pass);
int sd_mount_info(void);
int sd_file_test(void);
int sd_command(const char *line);

int video_init(void);
int video_command(const char *line);
void video_status(void);

int video_stop(void);
