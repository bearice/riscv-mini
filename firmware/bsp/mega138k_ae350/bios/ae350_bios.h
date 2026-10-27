#pragma once
#include "bios.h"
void bios_putc(char c);
void bios_puts(const char *s);
int bios_getc(void);
uint32_t bios_time_ms(void);
void bios_poll(void);
int bios_mount(void);
int bios_unmount(void);
int bios_fs_open(const char *path,unsigned flags);
int bios_fs_close(int handle);
int bios_fs_read(int handle,void *data,unsigned bytes);
int bios_fs_write(int handle,const void *data,unsigned bytes);
int bios_fs_seek(int handle,int offset,int whence);
int bios_fs_stat(const char *path,struct bios_fs_stat *out);
int bios_file_read(const char *path,unsigned offset,void *data,unsigned capacity);
int bios_file_size(const char *path);
void bios_command(char *line);
void bios_editor(void);
void bios_runtime_init(void);
extern struct bios_info ae350_bios_info;
