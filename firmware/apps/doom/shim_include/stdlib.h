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
/* Match abs(int)'s argument conversion and single evaluation. In particular,
 * R_StoreWallRange passes an unsigned angle difference: a macro preserves its
 * unsigned type, loses negative angles, and projects walls at near-zero depth.
 * Unsigned negation also preserves the target's INT_MIN wrap behaviour. */
static inline int abs(int x) { return x < 0 ? (int)(0u - (unsigned int)x) : x; }
#endif
