#include <stddef.h>
void *memcpy(void *dest, const void *source, size_t n) {
    unsigned char *d=dest; const unsigned char *s=source;
    for (size_t i=0; i<n; ++i) d[i]=s[i];
    return dest;
}
void *memset(void *dest, int value, size_t n) {
    unsigned char *d=dest;
    for (size_t i=0; i<n; ++i) d[i]=(unsigned char)value;
    return dest;
}
int memcmp(const void *left, const void *right, size_t n) {
    const unsigned char *a=left, *b=right;
    for (size_t i=0; i<n; ++i) if (a[i]!=b[i]) return (int)a[i]-(int)b[i];
    return 0;
}
size_t strlen(const char *s) { size_t n=0; while(s[n]) ++n; return n; }
int strcmp(const char *a, const char *b) {
    while (*a && *a==*b) { ++a; ++b; }
    return (unsigned char)*a-(unsigned char)*b;
}
char *strchr(const char *s, int ch) {
    do { if ((unsigned char)*s==(unsigned char)ch) return (char *)s; } while (*s++);
    return 0;
}
