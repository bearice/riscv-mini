#pragma once
#include "include/bios.h"
#include <hal/hal.h>
#include <stddef.h>
struct bios_settings {uint32_t magic,version,boot,delay_ms;uint8_t ip[4],server[4];char file[64];};
extern struct bios_settings bios_settings;
void bios_console_init(unsigned lcd_ok);
uintptr_t bios_console_grid(void);   /* 当前活动 TTY 网格基址，供 `md` 诊断读取 */
void bios_console_uart_only(int on); /* 临时静默 LCD 镜像，让诊断 dump 只走 UART */
void bios_putc(char c);
void bios_puts(const char *s);
void bios_hex(uint32_t value);
void bios_decimal(uint32_t value);
void bios_console_poll(void);
int bios_getc(void);
int bios_mouse_take(struct bios_mouse *event);
int bios_key_take(struct bios_key *event);
int bios_video_mode(unsigned mode);
void bios_settings_default(void);
int bios_settings_load(void);
int bios_settings_save(void);
int bios_sd_boot(const char *path);
void bios_sd_list(void);
void bios_sd_list_dir(const char *path);
void bios_sd_remove(const char *path);
void bios_sd_cat(const char *path,unsigned max);
void bios_sd_stat(const char *path);
void bios_sd_mkdir(const char *path);
void bios_sd_move(const char *from,const char *to);
int bios_net_boot(const uint8_t server[4],const char *path);
int bios_fetch(const char *path);
int bios_fetch_raw(const char *path);
int bios_tftp_get(const uint8_t server[4],const char *path,void *dest,unsigned capacity,unsigned *length);
int bios_ping(const char *target,unsigned count);
int parse_ip(const char *text,uint8_t ip[4]);
int bios_file_read(const char *path,unsigned offset,void *data,unsigned capacity);
int bios_file_size(const char *path);
/* File system layer over FatFs: the ecall services, the console commands and the
 * legacy FILE_READ/FILE_SIZE are all clients of these. */
int bios_fs_open(const char *path,unsigned flags);
int bios_fs_close(int handle);
int bios_fs_read(int handle,void *data,unsigned length);
int bios_fs_write(int handle,const void *data,unsigned length);
int bios_fs_seek(int handle,int offset,int whence);
int bios_fs_stat(const char *path,struct bios_fs_stat *out);
int bios_fs_mkdir(const char *path);
int bios_fs_unlink(const char *path);
int bios_fs_rename(const char *from,const char *to);
int bios_payload_run(const void *image,unsigned length);
int bios_payload_check(const void *image,unsigned length);
int bios_self_test(void);
int bios_exception_hook(hal_trap_frame_t *frame);
void bios_post(void);
void bios_poll(void);
int bios_benchmark_command(const char *command);
void bios_enter(unsigned entry,const struct bios_info *info,unsigned stack);

/* One synchronous loader owns progress; dots represent completed 64 KiB. */
void bios_load_begin(const char *source);
void bios_load_progress(unsigned bytes);
void bios_load_end(unsigned bytes,int ok);
void bios_boot_timing(const char *phase,unsigned start);
