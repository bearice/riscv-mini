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
int lcd_show(unsigned sd_ready);
int lcd_network(const uint8_t mac[6],const uint8_t ip[4]);
int lcd_link(unsigned up);
int sd_mount(void);
int sd_list(void);

int video_init(void);
volatile uint16_t *video_frame(unsigned slot);
int video_present(unsigned slot);
void video_status(void);

int video_stop(void);
