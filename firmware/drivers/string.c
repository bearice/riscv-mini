#include <stddef.h>
#include <stdint.h>
/* may_alias permits word accesses to arbitrary object representations. Only
 * aligned, fully contained words are accessed; tails never overread a buffer. */
typedef uint32_t __attribute__((may_alias)) memory_word;
void *memcpy(void *dest, const void *source, size_t n) {
    unsigned char *d=dest; const unsigned char *s=source;
    if(!(((uintptr_t)d^(uintptr_t)s)&3u)) {
        while(n && ((uintptr_t)d&3u)) {*d++=*s++;--n;}
        while(n>=16) {
            memory_word *dw=(memory_word *)d;const memory_word *sw=(const memory_word *)s;
            dw[0]=sw[0];dw[1]=sw[1];dw[2]=sw[2];dw[3]=sw[3];
            d+=16;s+=16;n-=16;
        }
        while(n>=4) {*(memory_word *)d=*(const memory_word *)s;d+=4;s+=4;n-=4;}
    }
    while(n--) *d++=*s++;
    return dest;
}
void *memset(void *dest, int value, size_t n) {
    unsigned char *d=dest;
    unsigned char byte=(unsigned char)value;
    while(n && ((uintptr_t)d&3u)) {*d++=byte;--n;}
    memory_word word=(uint32_t)byte*0x01010101u;
    while(n>=16) {
        memory_word *dw=(memory_word *)d;
        dw[0]=word;dw[1]=word;dw[2]=word;dw[3]=word;d+=16;n-=16;
    }
    while(n>=4) {*(memory_word *)d=word;d+=4;n-=4;}
    while(n--) *d++=byte;
    return dest;
}
int memcmp(const void *left, const void *right, size_t n) {
    const unsigned char *a=left, *b=right;
    if(!(((uintptr_t)a^(uintptr_t)b)&3u)) {
        while(n && ((uintptr_t)a&3u)) {if(*a!=*b)return (int)*a-(int)*b;++a;++b;--n;}
        while(n>=4 && *(const memory_word *)a==*(const memory_word *)b) {a+=4;b+=4;n-=4;}
    }
    while(n--) {if(*a!=*b)return (int)*a-(int)*b;++a;++b;}
    return 0;
}
size_t strlen(const char *s) { size_t n=0; while(s[n]) ++n; return n; }
int strcmp(const char *a, const char *b) {
    while (*a && *a==*b) { ++a; ++b; }
    return (unsigned char)*a-(unsigned char)*b;
}
int strncmp(const char *a,const char *b,size_t n) {
    while(n--) {unsigned char x=*a++,y=*b++;if(x!=y)return (int)x-(int)y;if(!x)return 0;}
    return 0;
}
char *strchr(const char *s, int ch) {
    do { if ((unsigned char)*s==(unsigned char)ch) return (char *)s; } while (*s++);
    return 0;
}
