/* BIOS payload shim: heap from static array, exit longjmps to dg_bios. */
#ifndef SHIM_STDLIB_H
#define SHIM_STDLIB_H
#include <stddef.h>
#define EXIT_SUCCESS 0
#define EXIT_FAILURE 1
void *malloc(size_t);
void *calloc(size_t,size_t);
void *realloc(void*,size_t);
void free(void*);
int atoi(const char*);
long atol(const char*);
double atof(const char*);
long strtol(const char*,char**,int);
unsigned long strtoul(const char*,char**,int);
void exit(int);
void abort(void);
char *getenv(const char*);
#define abs(x) ((x)<0?-(x):(x))
#endif
