/* Compile string.c with renamed symbols to compare with the host libc.
 * cc -fno-builtin -fsanitize=address,undefined tests/libc_memory_test.c ... */
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#define memcpy mini_memcpy
#define memset mini_memset
#define memcmp mini_memcmp
#define strlen mini_strlen
#define strcmp mini_strcmp
#define strncmp mini_strncmp
#define strchr mini_strchr
#include "../firmware/drivers/string.c"
#undef memcpy
#undef memset
#undef memcmp
#undef strlen
#undef strcmp
#undef strncmp
#undef strchr
static unsigned char source[65568],dest[65568],expected[65568];
static int reference(const unsigned char *a,const unsigned char *b,size_t n) {
    for(size_t i=0;i<n;++i)if(a[i]!=b[i])return (int)a[i]-(int)b[i];return 0;
}
static void check(size_t n,unsigned so,unsigned d) {
    memset(dest,0x7c,sizeof(dest));memset(expected,0x7c,sizeof(expected));
    memcpy(expected+d,source+so,n);
    assert(mini_memcpy(dest+d,source+so,n)==dest+d);
    assert(!memcmp(dest,expected,sizeof(dest)));
    assert(mini_memcmp(source+so,dest+d,n)==0);
    if(n) {
        size_t indexes[]={0,n/2,n-1};
        for(unsigned i=0;i<3;++i) {
            dest[d+indexes[i]]^=0x80;
            assert(mini_memcmp(source+so,dest+d,n)==reference(source+so,dest+d,n));
            dest[d+indexes[i]]^=0x80;
        }
    }
    memset(dest,0x7c,sizeof(dest));memset(expected,0x7c,sizeof(expected));
    memset(expected+d,0x1a5,n);
    assert(mini_memset(dest+d,0x1a5,n)==dest+d);
    assert(!memcmp(dest,expected,sizeof(dest)));
}
int main(void) {
    for(size_t i=0;i<sizeof(source);++i)source[i]=(i*173u+i/7u)&255u;
    for(unsigned so=0;so<8;++so)for(unsigned d=0;d<8;++d) {
        for(size_t n=0;n<=257;++n)check(n,so,d);
        const size_t sizes[]={511,512,513,4095,4096,4097,65536};
        for(unsigned i=0;i<sizeof(sizes)/sizeof(sizes[0]);++i)check(sizes[i],so,d);
    }
    /* Exact page-end objects expose reads/writes beyond n, including zero n. */
    size_t page=(size_t)sysconf(_SC_PAGESIZE);
    unsigned char *s=mmap(0,page*2,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
    unsigned char *d=mmap(0,page*2,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
    assert(s!=MAP_FAILED && d!=MAP_FAILED);
    assert(!mprotect(s+page,page,PROT_NONE) && !mprotect(d+page,page,PROT_NONE));
    memset(s,0x83,page);
    for(size_t n=0;n<=257;++n) {
        assert(mini_memcpy(d+page-n,s+page-n,n)==d+page-n);
        assert(!mini_memcmp(d+page-n,s+page-n,n));
        mini_memset(d+page-n,0x83,n);
    }
    munmap(s,page*2);munmap(d,page*2);
    puts("libc memory alignment, guards and comparison PASS");
}
