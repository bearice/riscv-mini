/* BIOS payload shim: stdio over BIOS_WRITE / BIOS_FILE_READ / BIOS_FILE_SIZE. */
#ifndef SHIM_STDIO_H
#define SHIM_STDIO_H
#include <stddef.h>
#define EOF (-1)
#define SEEK_SET 0
#define SEEK_CUR 1
#define SEEK_END 2
#define BUFSIZ 1024
#define FILENAME_MAX 64
#define EOF_MARKER_UNUSED 0
typedef struct _shim_FILE FILE;
extern FILE *stdin,*stdout,*stderr;
int printf(const char*,...);
int fprintf(FILE*,const char*,...);
int vfprintf(FILE*,const char*,__builtin_va_list);
int snprintf(char*,size_t,const char*,...);
int vsnprintf(char*,size_t,const char*,__builtin_va_list);
int sscanf(const char*,const char*,...);
int puts(const char*);
int putchar(int);
int fputc(int,FILE*);
int fputs(const char*,FILE*);
FILE *fopen(const char*,const char*);
int fclose(FILE*);
size_t fread(void*,size_t,size_t,FILE*);
size_t fwrite(const void*,size_t,size_t,FILE*);
int fseek(FILE*,long,int);
long ftell(FILE*);
int fflush(FILE*);
int feof(FILE*);
int ferror(FILE*);
int remove(const char*);
int rename(const char*,const char*);
void rewind(FILE*);
#endif
