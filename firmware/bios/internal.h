#pragma once
#include "include/bios.h"
#include <hal/hal.h>
#include <stddef.h>
struct bios_settings {uint32_t magic,version,boot,delay_ms;uint8_t ip[4],server[4];char file[64];};
extern struct bios_settings bios_settings;
void bios_console_init(unsigned lcd_ok);
void bios_putc(char c);
void bios_puts(const char *s);
void bios_hex(uint32_t value);
void bios_decimal(uint32_t value);
void bios_console_poll(void);
int bios_getc(void);
int bios_mouse_take(struct bios_mouse *event);
int bios_video_mode(unsigned mode);
void bios_settings_default(void);
int bios_settings_load(void);
int bios_settings_save(void);
int bios_sd_boot(const char *path);
void bios_sd_list(void);
int bios_net_boot(const uint8_t server[4],const char *path);
int bios_fetch(const char *path);
int bios_tftp_get(const uint8_t server[4],const char *path,void *dest,unsigned capacity,unsigned *length);
int bios_file_read(const char *path,unsigned offset,void *data,unsigned capacity);
int bios_payload_run(const void *image,unsigned length);
int bios_payload_check(const void *image,unsigned length);
int bios_self_test(void);
int bios_exception_hook(hal_trap_frame_t *frame);
void bios_post(void);
void bios_poll(void);
void bios_enter(unsigned entry,const struct bios_info *info,unsigned stack);
