/* Newlib syscall adapter for the cooperative BIOS ABI. No replacement stdio,
 * allocator, string routines or arithmetic: those come from the toolchain. */
#include "runtime.h"
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <setjmp.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <unistd.h>

#ifndef BIOS_LIBC_HEAP_BYTES
#define BIOS_LIBC_HEAP_BYTES (8u * 1024u * 1024u)
#endif
#define FD_COUNT 9 /* stdin/out/err plus ABI v1's six filesystem handles */
static unsigned char heap[BIOS_LIBC_HEAP_BYTES] __attribute__((aligned(16)));
static size_t heap_used;
static struct { int used, handle, flags; } files[FD_COUNT];
static struct bios_libc_io io;
static unsigned last_poll;
static int polling, armed, exit_status;
static jmp_buf exit_context;
static char *empty_environment[] = { NULL };
char **environ = empty_environment;

__attribute__((weak)) void bios_libc_poll_hook(void) {}
__attribute__((weak)) void bios_libc_read_hook(const void *data, unsigned size) {
    (void)data; (void)size;
}
__attribute__((weak)) int bios_libc_console_busy(void) { return 0; }
static unsigned now(void) { return (unsigned)bios_call(BIOS_TIME,0,0,0,0); }
static void poll_io(void) {
    unsigned time = now();
    if (polling || (unsigned)(time-last_poll) < 8) return;
    polling = 1; last_poll = time;
    bios_call(BIOS_POLL,0,0,0,0);
    io.poll_ms += now()-time; ++io.poll_calls;
    bios_libc_poll_hook();
    polling = 0;
}
static int fs(unsigned fn, uintptr_t a, uintptr_t b, uintptr_t c) {
    unsigned time = now();
    int result = bios_call(fn,a,b,c,0);
    io.fs_ms += now()-time; ++io.fs_calls;
    return result;
}
void bios_libc_io_snapshot(struct bios_libc_io *out) { *out = io; }
static int error(int code) { errno = code; return -1; }
static int path_name(const char *input, char out[64]) {
    if (!input) return error(EFAULT);
    while (input[0]=='.' && (input[1]=='/' || input[1]=='\\')) input += 2;
    while (*input=='/' || *input=='\\') ++input;
    unsigned n = 0;
    while (input[n] && n<63) { out[n]=input[n]=='\\' ? '/' : input[n]; ++n; }
    if (input[n]) return error(ENAMETOOLONG);
    out[n]=0;
    return 0;
}
static int valid(int fd) { return fd>=3 && fd<FD_COUNT && files[fd].used; }

int _open(const char *path, int flags, ...) {
    char name[64];
    if (path_name(path,name)) return -1;
    int access = flags & O_ACCMODE;
    if (access!=O_RDONLY && access!=O_WRONLY && access!=O_RDWR) return error(EINVAL);
    /* ABI v1 opens READ|WRITE as write-only. Do not claim r+/w+/a+ works
     * until the BIOS exposes correct combined access. Read and write streams
     * opened separately are supported on the unchanged BIOS. */
    if (access==O_RDWR) return error(ENOTSUP);
    if ((flags & (O_TRUNC|O_APPEND)) && access==O_RDONLY) return error(EINVAL);
    int fd;
    for (fd=3; fd<FD_COUNT && files[fd].used; ++fd) {}
    if (fd==FD_COUNT) return error(EMFILE);
    unsigned mode = access==O_RDONLY ? BIOS_FS_O_READ : BIOS_FS_O_WRITE;
    struct bios_fs_stat st;
    /* BIOS O_CREATE is CREATE_NEW, not POSIX O_CREAT. Check existence so
     * O_CREAT without O_EXCL preserves an existing file. BIOS is single-app. */
    int exists = fs(BIOS_FS_STAT,(uintptr_t)name,(uintptr_t)&st,0)==0;
    if ((flags&O_CREAT) && (flags&O_EXCL) && exists) return error(EEXIST);
    if (!exists && !(flags&O_CREAT)) return error(ENOENT);
    if (!exists) mode |= BIOS_FS_O_CREATE;
    if (flags&O_TRUNC) mode |= BIOS_FS_O_TRUNC;
    if (flags&O_APPEND) mode |= BIOS_FS_O_APPEND;
    /* CREATE_NEW needs write access on this BIOS. Create, close and reopen
     * read-only when POSIX O_RDONLY|O_CREAT requested a missing file. */
    if (!exists && access==O_RDONLY) {
        int created=fs(BIOS_FS_OPEN,(uintptr_t)name,BIOS_FS_O_WRITE|BIOS_FS_O_CREATE,0);
        if (created<0) return error(EIO);
        if (fs(BIOS_FS_CLOSE,created,0,0)<0) return error(EIO);
        mode=BIOS_FS_O_READ;
    }
    int handle = fs(BIOS_FS_OPEN,(uintptr_t)name,mode,0);
    if (handle<0) return error(EIO); /* ABI v1 collapses FatFs errors to -1. */
    files[fd].used=1; files[fd].handle=handle; files[fd].flags=flags;
    return fd;
}
int _close(int fd) {
    if (fd>=0 && fd<3) return 0;
    if (!valid(fd)) return error(EBADF);
    int result=fs(BIOS_FS_CLOSE,files[fd].handle,0,0);
    files[fd].used=0;
    return result<0 ? error(EIO) : 0;
}
int _read(int fd, void *buffer, size_t length) {
    if (length>INT_MAX) return error(EINVAL);
    if (fd!=0 && (!valid(fd) || (files[fd].flags&O_ACCMODE)==O_WRONLY)) return error(EBADF);
    if (!length) return 0;
    if (fd==0) {
        /* Only read stdin explicitly: polling never steals debugger input. */
        int c;
        while ((c=bios_call(BIOS_GETC,0,0,0,0))<0) poll_io();
        ((unsigned char *)buffer)[0]=(unsigned char)c;
        return 1;
    }
    unsigned done=0;
    while (done<length) {
        unsigned cap=length-done; if (cap>4096) cap=4096;
        int got=fs(BIOS_FS_READ,files[fd].handle,(uintptr_t)((char *)buffer+done),cap);
        if (got<0) return done ? (int)done : error(EIO);
        if (!got) break;
        bios_libc_read_hook((char *)buffer+done,(unsigned)got);
        done+=(unsigned)got; poll_io();
        if ((unsigned)got<cap) break;
    }
    return (int)done;
}
int _write(int fd, const void *buffer, size_t length) {
    if (length>INT_MAX) return error(EINVAL);
    int console = fd==1 || fd==2;
    if (!console && (!valid(fd) || (files[fd].flags&O_ACCMODE)==O_RDONLY)) return error(EBADF);
    if (console && bios_libc_console_busy()) return (int)length;
    if (!console && (files[fd].flags&O_APPEND)) {
        if (fs(BIOS_FS_SEEK,files[fd].handle,0,BIOS_FS_END)<0) return error(EIO);
    }
    unsigned done=0;
    while (done<length) {
        unsigned cap=length-done; if (cap>4096) cap=4096;
        int got=console ? bios_call(BIOS_WRITE,(uintptr_t)((const char *)buffer+done),cap,0,0)
                        : fs(BIOS_FS_WRITE,files[fd].handle,(uintptr_t)((const char *)buffer+done),cap);
        if (got<=0) return done ? (int)done : error(EIO);
        done+=(unsigned)got;
        if (!console) poll_io();
    }
    return (int)done;
}
off_t _lseek(int fd, off_t offset, int whence) {
    if (fd>=0 && fd<3) return error(ESPIPE);
    if (!valid(fd)) return error(EBADF);
    if (whence!=SEEK_SET && whence!=SEEK_CUR && whence!=SEEK_END) return error(EINVAL);
    /* BIOS clamps negative seeks; POSIX requires EINVAL. Compute the target
     * in 64 bits before issuing a SET, and never pass INT_MIN to BIOS negation. */
    int base=0;
    if (whence==SEEK_CUR) base=fs(BIOS_FS_SEEK,files[fd].handle,0,BIOS_FS_CUR);
    else if (whence==SEEK_END) {
        int saved=fs(BIOS_FS_SEEK,files[fd].handle,0,BIOS_FS_CUR);
        base=fs(BIOS_FS_SEEK,files[fd].handle,0,BIOS_FS_END);
        if (saved<0 || fs(BIOS_FS_SEEK,files[fd].handle,saved,BIOS_FS_SET)<0) return error(EIO);
    }
    if (base<0) return error(EIO);
    int64_t target=(int64_t)base+offset;
    if (target<0 || target>INT_MAX) return error(EINVAL);
    int result=fs(BIOS_FS_SEEK,files[fd].handle,(uintptr_t)target,BIOS_FS_SET);
    return result<0 ? error(EIO) : result;
}
static void fill_stat(struct stat *out, const struct bios_fs_stat *in) {
    memset(out,0,sizeof *out);
    out->st_mode=(in->attrib&0x10 ? S_IFDIR : S_IFREG) | 0666;
    out->st_size=in->size; out->st_blksize=4096; out->st_nlink=1;
}
int _stat(const char *path, struct stat *out) {
    char name[64]; struct bios_fs_stat st;
    if (path_name(path,name)) return -1;
    if (fs(BIOS_FS_STAT,(uintptr_t)name,(uintptr_t)&st,0)<0) return error(ENOENT);
    fill_stat(out,&st); return 0;
}
int _fstat(int fd, struct stat *out) {
    if (fd>=0 && fd<3) {
        memset(out,0,sizeof *out); out->st_mode=S_IFCHR|0666; out->st_blksize=4096;
        return 0;
    }
    if (!valid(fd)) return error(EBADF);
    /* Query the open handle's size, so rename/unlink do not invalidate fstat. */
    int pos=fs(BIOS_FS_SEEK,files[fd].handle,0,BIOS_FS_CUR);
    int end=fs(BIOS_FS_SEEK,files[fd].handle,0,BIOS_FS_END);
    int restored=pos<0 ? -1 : fs(BIOS_FS_SEEK,files[fd].handle,pos,BIOS_FS_SET);
    if (end<0 || restored<0) return error(EIO);
    struct bios_fs_stat st={(unsigned)end,0}; fill_stat(out,&st); return 0;
}
int _isatty(int fd) { if (fd>=0 && fd<3) return 1; return valid(fd) ? 0 : error(EBADF); }
int _unlink(const char *path) {
    char name[64]; if (path_name(path,name)) return -1;
    return fs(BIOS_FS_UNLINK,(uintptr_t)name,0,0)<0 ? error(EIO) : 0;
}
int mkdir(const char *path, mode_t mode) {
    (void)mode; char name[64]; if (path_name(path,name)) return -1;
    struct bios_fs_stat st;
    if (fs(BIOS_FS_STAT,(uintptr_t)name,(uintptr_t)&st,0)==0) return error(EEXIST);
    return fs(BIOS_FS_MKDIR,(uintptr_t)name,0,0)<0 ? error(EIO) : 0;
}
int rename(const char *from, const char *to) {
    char a[64],b[64]; if (path_name(from,a) || path_name(to,b)) return -1;
    /* FatFs does not replace an existing destination. Report that explicitly;
     * do not unlink it first and risk losing data if rename subsequently fails. */
    if (!strcmp(a,b)) { struct stat st; return _stat(from,&st); }
    struct bios_fs_stat st;
    if (fs(BIOS_FS_STAT,(uintptr_t)b,(uintptr_t)&st,0)==0) return error(EEXIST);
    return fs(BIOS_FS_RENAME,(uintptr_t)a,(uintptr_t)b,0)<0 ? error(EIO) : 0;
}
void *_sbrk(ptrdiff_t increment) {
    if (increment>=0) {
        if ((size_t)increment>sizeof heap-heap_used) { errno=ENOMEM; return (void *)-1; }
    } else if ((size_t)(-(increment+1))+1>heap_used) { errno=ENOMEM; return (void *)-1; }
    void *old=heap+heap_used;
    heap_used+=increment;
    return old;
}
int _getpid(void) { return 1; }
int _kill(int pid,int signal) { (void)pid; (void)signal; return error(ENOSYS); }
int _link(const char *from,const char *to) { (void)from; (void)to; return error(ENOSYS); }
int _gettimeofday(struct timeval *tv, void *tz) {
    (void)tz; unsigned ms=now(); tv->tv_sec=ms/1000; tv->tv_usec=(ms%1000)*1000; return 0;
}
void _exit(int status) {
    exit_status=status;
    for (int fd=3; fd<FD_COUNT; ++fd) if (files[fd].used) _close(fd);
    if (armed) longjmp(exit_context,1);
    for (;;) bios_call(BIOS_POLL,0,0,0,0);
}
/* Required by Newlib constructor/finalizer helpers with our custom crt0. */
void _init(void) {}
void _fini(void) {}
extern void __libc_init_array(void);
extern void __libc_fini_array(void);
int bios_libc_run(const struct bios_info *info, int (*main_fn)(const struct bios_info *)) {
    armed=1;
    if (!setjmp(exit_context)) {
        setvbuf(stdout,NULL,_IONBF,0); setvbuf(stderr,NULL,_IONBF,0);
        __libc_init_array(); atexit(__libc_fini_array);
        exit(main_fn(info));
    }
    armed=0;
    return exit_status;
}
