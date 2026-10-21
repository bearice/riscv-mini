/* Reusable app-level contract test. Creates one exclusive temporary SD file,
 * removes it before returning, and never overwrites an existing user file. */
#include "runtime.h"
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define NAME "LIBCTEST.TMP"
#define MOVED "LIBCTEST.MOV"
static int constructed, finalized, callback, owns_file;
static FILE *stream;
static unsigned char data[9000], copy[9000];
static unsigned available_handles(void) {
    int handles[6]; unsigned available=0;
    for(unsigned i=0;i<6;++i) {
        handles[i]=bios_call(BIOS_FS_OPEN,(uintptr_t)"DOOM1.WAD",BIOS_FS_O_READ,0,0);
        if(handles[i]>=0)++available;
    }
    for(unsigned i=0;i<6;++i)if(handles[i]>=0)bios_call(BIOS_FS_CLOSE,handles[i],0,0,0);
    return available;
}
__attribute__((constructor)) static void init_test(void) { constructed=1; }
__attribute__((destructor)) static void fini_test(void) { finalized=1; }
static void exit_callback(void) { callback=1; puts("LIBC atexit OK"); }
extern void *_sbrk(ptrdiff_t increment);
#define CHECK(test, n) do { if (!(test)) { printf("LIBC FAIL %d errno=%d\n", n,errno); return n; } } while (0)

static int test_main(const struct bios_info *info) {
    (void)info;
    CHECK(constructed,1);
    CHECK(atexit(exit_callback)==0,2);
    unsigned angle=0xf0000000u;
    CHECK((unsigned)abs(angle)==0x10000000u,3);
    char text[64];
    CHECK(snprintf(text,sizeof text,"%08x %.2f",0x12u,1.25)==13,4);
    CHECK(!strcmp(text,"00000012 1.25"),5);
    int decimal; unsigned hex; double real;
    CHECK(sscanf("-17 ff 2.5","%d %x %lf",&decimal,&hex,&real)==3 && decimal==-17 && hex==255 && real==2.5,6);
    char *end;
    errno=0; CHECK(strtol("2147483648",&end,10)==LONG_MAX && errno==ERANGE && !*end,7);
    CHECK(sqrt(9.0)==3.0 && floor(-1.5)==-2.0,8);
    unsigned char *memory=calloc(257,1); CHECK(memory,9);
    for (unsigned i=0;i<257;++i) CHECK(memory[i]==0,10);
    memset(memory,0x5a,257); memory=realloc(memory,1024); CHECK(memory && memory[256]==0x5a,11); free(memory);
    void *before=_sbrk(0);
    errno=0; CHECK(_sbrk(INT_MAX)==(void *)-1 && errno==ENOMEM && _sbrk(0)==before,12);
    errno=0; CHECK(fopen("LIBCTEST.ABSENT","rb")==NULL && errno==ENOENT,13);
    errno=0; CHECK(fopen("DOOM1.WAD","r+")==NULL && errno==ENOTSUP,14);
    char long_path[80]; memset(long_path,'x',sizeof long_path-1); long_path[79]=0;
    errno=0; CHECK(fopen(long_path,"rb")==NULL && errno==ENAMETOOLONG,15);
    struct stat st; CHECK(stat(NAME,&st)<0 && stat(MOVED,&st)<0,16);
    stream=fopen(NAME,"wbx"); CHECK(stream,17); owns_file=1;
    for (unsigned i=0;i<sizeof data;++i)data[i]=(unsigned char)(i*17+3);
    CHECK(fwrite(data,1,sizeof data,stream)==sizeof data && fclose(stream)==0,18); stream=NULL;
    CHECK(stat(NAME,&st)==0 && st.st_size==sizeof data,19);
    errno=0; CHECK(open(NAME,O_WRONLY|O_CREAT|O_EXCL,0666)==-1 && errno==EEXIST,20);
    stream=fopen(NAME,"rb"); CHECK(stream,21);
    CHECK(fread(copy,1,sizeof copy,stream)==sizeof copy && !memcmp(copy,data,sizeof copy),22);
    CHECK(fread(copy,1,1,stream)==0 && feof(stream),23);
    CHECK(fseek(stream,17,SEEK_SET)==0 && !feof(stream) && ftell(stream)==17,24);
    CHECK(fread(copy,3,7,stream)==7 && !memcmp(copy,data+17,21),25);
    CHECK(fclose(stream)==0,26); stream=NULL;
    int fd=open(NAME,O_RDONLY); CHECK(fd>=3,27);
    CHECK(lseek(fd,9,SEEK_SET)==9,28);
    errno=0; CHECK(lseek(fd,-9001,SEEK_END)==-1 && errno==EINVAL && lseek(fd,0,SEEK_CUR)==9,29);
    CHECK(fstat(fd,&st)==0 && st.st_size==9000 && lseek(fd,0,SEEK_CUR)==9 && close(fd)==0,30);
    stream=fopen(NAME,"ab"); CHECK(stream,31);
    CHECK(fseek(stream,0,SEEK_SET)==0 && fwrite("END",1,3,stream)==3 && fclose(stream)==0,32); stream=NULL;
    stream=fopen(NAME,"rb"); CHECK(stream && fseek(stream,-3,SEEK_END)==0,33);
    CHECK(fread(copy,1,3,stream)==3 && !memcmp(copy,"END",3) && fclose(stream)==0,34); stream=NULL;
    CHECK(rename(NAME,MOVED)==0 && remove(MOVED)==0,35); owns_file=0;
    /* Deliberately leave raw descriptors open: _exit must close these too. */
    CHECK(open("DOOM1.WAD",O_RDONLY)>=3,36);
    puts("LIBC contracts OK");
    exit(42);
}

int payload_main(const struct bios_info *info) {
    unsigned before=available_handles();
    int status=bios_libc_run(info,test_main);
    if(stream)fclose(stream);
    if(owns_file) { remove(NAME); remove(MOVED); }
    if(status!=42 || !callback || !finalized) {
        char message[96];
        int n=snprintf(message,sizeof message,"LIBC TEST FAIL status=%d callback=%d finalized=%d\r\n",status,callback,finalized);
        bios_call(BIOS_WRITE,(uintptr_t)message,n,0,0);
        return 1;
    }
    /* Preserve foreign BIOS handles: compare available capacity before/after,
     * rather than assuming a prior app left all six slots empty. */
    unsigned after=available_handles();
    int ok=before>0 && before==after;
    char counts[80];
    int n=snprintf(counts,sizeof counts,"LIBC handles before=%u after=%u\r\n",before,after);
    bios_call(BIOS_WRITE,(uintptr_t)counts,n,0,0);
    const char *message=ok ? "LIBC TEST PASS\r\n" : "LIBC TEST FAIL leaked handles\r\n";
    bios_call(BIOS_WRITE,(uintptr_t)message,strlen(message),0,0);
    return ok ? 0 : 1;
}
