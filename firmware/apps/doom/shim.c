/* Minimal libc for the DOOM BIOS payload (see shim_include/).
 * Console output -> BIOS_WRITE (UART log). Files are read-only through
 * BIOS_FILE_READ/BIOS_FILE_SIZE (FatFs on SD). Heap is a static BSS array.
 * exit() longjmps back into dg_bios via shim_setjmp. */
#include "../../bios/include/bios.h"
#include "shim_private.h"
#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>
/* The shim's own headers: they declare exactly what this file implements.
 * memcpy/memset/memcmp/strlen/strcmp/strncmp/strchr come from
 * firmware/drivers/string.c (linked via --helper string). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <ctype.h>
#include <errno.h>
#include <math.h>
#include <sys/stat.h>

int errno;

static int isspace_(int c) {return c==' '||c=='\t'||c=='\n'||c=='\r'||c=='\f'||c=='\v';}
static int tolower_(int c) {return c>='A'&&c<='Z'?c+32:c;}
static int hexval(int c) {
    if(c>='0'&&c<='9')return c-'0';
    if(c>='a'&&c<='f')return c-'a'+10;
    if(c>='A'&&c<='F')return c-'A'+10;
    return -1;
}

/* ------------------------------------------------------------------ console */
static void con_write(const char *s,size_t n) {
    while(n) {unsigned k=n>4096?4096:(unsigned)n;
        bios_call(BIOS_WRITE,(uintptr_t)s,k,0,0);s+=k;n-=k;}
}

/* --------------------------------------------------------------------- FILE
 * Thin stdio over the BIOS file system services (FS_OPEN/READ/WRITE/SEEK/
 * CLOSE/MKDIR/UNLINK/RENAME); the handle does the buffering on the BIOS side. */
struct _shim_FILE {int used,console,handle;uint32_t pos,size;int err;};
static struct _shim_FILE file_table[12];
static struct _shim_FILE console_in = {1,1,-1,0,0,0};
static struct _shim_FILE console_out = {1,1,-1,0,0,0};
FILE *stdin=&console_in,*stdout=&console_out,*stderr=&console_out;

/* FatFs takes relative paths; DOOM builds "./x" and "/x" from configdir.
 * Both mean SD root here. Returns length, -1 when too long. */
static int normalize_path(const char *src,char *dst,unsigned cap) {
    while(src[0]=='.'&&(src[1]=='/'||src[1]=='\\'))src+=2;
    while(src[0]=='/'||src[0]=='\\')++src;
    unsigned n=0;
    for(;src[n]&&n<cap-1;++n)dst[n]=src[n]=='\\'?'/':src[n];
    if(src[n])return -1;
    dst[n]=0;
    return (int)n;
}

/* Attribution for the silent phases (R_Init, level load): time spent inside
 * FS_* and POLL ecalls is accumulated separately and printed with the wall
 * clock by io_report().  "reads grew slowly" then splits cleanly into BIOS file
 * I/O, the service pump, and payload CPU time without a debugger. */
static unsigned long fs_ms,fs_calls,poll_ms,poll_calls;
static unsigned now_ms(void) {return (unsigned)bios_call(BIOS_TIME,0,0,0,0);}
static int fs_call(unsigned fn,uintptr_t a1,uintptr_t a2,uintptr_t a3) {
    unsigned t=now_ms();int r=bios_call(fn,a1,a2,a3,0);fs_ms+=now_ms()-t;++fs_calls;return r;
}
static void poll_call(void) {
    unsigned t=now_ms();bios_call(BIOS_POLL,0,0,0,0);poll_ms+=now_ms()-t;++poll_calls;
}
/* Keep USB/console responsive during a long transfer without paying a POLL per
 * ecall: one pump per 8 ms is plenty (a POLL measured 0.55 ms on hardware). */
static unsigned last_pump;
static void io_pump(void) {
    unsigned t=now_ms();
    if((unsigned)(t-last_pump)<8)return;
    last_pump=t;
    bios_call(BIOS_POLL,0,0,0,0);poll_ms+=now_ms()-t;++poll_calls;
}

FILE *fopen(const char *path,const char *mode) {
    char name[64];
    if(!path||!mode) {errno=EINVAL;return 0;}
    if(normalize_path(path,name,sizeof name)<0) {errno=EINVAL;return 0;}
    unsigned flags=0;
    for(const char *m=mode;*m;++m) {
        if(*m=='r')flags|=BIOS_FS_O_READ;
        else if(*m=='w')flags|=BIOS_FS_O_WRITE|BIOS_FS_O_TRUNC;
        else if(*m=='a')flags|=BIOS_FS_O_WRITE|BIOS_FS_O_APPEND;
        else if(*m=='+')flags|=BIOS_FS_O_READ|BIOS_FS_O_WRITE;
    }
    if(!flags)flags=BIOS_FS_O_READ;
    int handle=fs_call(BIOS_FS_OPEN,(uintptr_t)name,flags,0);
    if(handle<0) {errno=(flags&BIOS_FS_O_WRITE)?EROFS:ENOENT;return 0;}
    for(unsigned i=0;i<sizeof file_table/sizeof*file_table;++i) {
        struct _shim_FILE *f=&file_table[i];
        if(f->used)continue;
        f->used=1;f->console=0;f->handle=handle;f->pos=0;f->size=0;f->err=0;
        int end=fs_call(BIOS_FS_SEEK,handle,0,BIOS_FS_END);
        if(end>=0) {
            f->size=(uint32_t)end;
            f->pos=(flags&BIOS_FS_O_APPEND)?(uint32_t)end:0;
            if(!(flags&BIOS_FS_O_APPEND))fs_call(BIOS_FS_SEEK,handle,0,BIOS_FS_SET);
        }
        return f;
    }
    fs_call(BIOS_FS_CLOSE,handle,0,0);
    errno=EMFILE;return 0;
}
int fclose(FILE *f) {
    if(!f) {errno=EBADF;return EOF;}
    if(f->console)return 0;
    int r=fs_call(BIOS_FS_CLOSE,f->handle,0,0);
    f->used=0;
    if(r!=0) {f->err=1;errno=EIO;return EOF;}
    return 0;
}
/* Progress log: the payload's long phases (R_Init, level load) are otherwise
 * silent for minutes, which makes a slow board indistinguishable from a hung
 * one.  UART is a log sink only; the game never reads it. */
static unsigned long io_bytes;
static unsigned long io_next_report=256ul*1024ul;
static void io_report(void) {
    if(io_bytes<io_next_report)return;
    io_next_report+=256ul*1024ul;
    printf("shim: reads %u KiB fs=%u ms/%u calls poll=%u ms/%u t=%u ms\n",
           (unsigned)(io_bytes>>10),(unsigned)fs_ms,(unsigned)fs_calls,
           (unsigned)poll_ms,(unsigned)poll_calls,now_ms());
}
/* One ecall per 4 KiB, never per item.  DOOM reads with fread(buf,1,n), so an
 * item loop here issued one FS_READ per byte: measured 268 127 calls and
 * 268 074 POLLs for 261 KiB, 219 s of a 298 s R_Init.  A byte stream has no
 * item boundaries; only the returned count is quantised, exactly like C. */
size_t fread(void *dst,size_t sz,size_t n,FILE *f) {
    if(!f||f->console||!sz) {errno=EBADF;return 0;}
    unsigned char *out=dst;size_t total=sz*n,done=0;
    while(done<total) {
        unsigned cap=(unsigned)(total-done);if(cap>4096)cap=4096;
        int r=fs_call(BIOS_FS_READ,f->handle,(uintptr_t)(out+done),cap);
        if(r<0) {f->err=1;errno=EIO;break;}
        if(r==0)break; /* EOF; a short count is how C reports it */
        done+=(unsigned)r;f->pos+=(unsigned)r;io_bytes+=(unsigned)r;
        io_pump();
    }
    io_report();
    return done/sz;
}
size_t fwrite(const void *src,size_t sz,size_t n,FILE *f) {
    if(!f||!sz) {errno=EBADF;return 0;}
    if(f->console) {con_write(src,sz*n);return n;}
    const unsigned char *in=src;size_t total=sz*n,done=0;
    while(done<total) {
        unsigned cap=(unsigned)(total-done);if(cap>4096)cap=4096;
        int w=fs_call(BIOS_FS_WRITE,f->handle,(uintptr_t)(in+done),cap);
        if(w<=0) {f->err=1;errno=EIO;break;}
        done+=(unsigned)w;f->pos+=(unsigned)w;
        io_pump();
    }
    return done/sz;
}
int fseek(FILE *f,long off,int whence) {
    if(!f) {errno=EBADF;return -1;}
    if(f->console) {long p=whence==SEEK_CUR?(long)f->pos+off:whence==SEEK_END?(long)f->size+off:off;
        if(p<0) {errno=EINVAL;return -1;} f->pos=(uint32_t)p;return 0;}
    int r=fs_call(BIOS_FS_SEEK,f->handle,(uintptr_t)(int)off,whence);
    if(r<0) {f->err=1;errno=EINVAL;return -1;}
    f->pos=(uint32_t)r;return 0;
}
long ftell(FILE *f) {if(!f) {errno=EBADF;return -1;}return (long)f->pos;}
/* The BIOS flushes on close; nothing else to do here. */
int fflush(FILE *f) {(void)f;return 0;}
int feof(FILE *f) {return f&&!f->console&&f->pos>=f->size;}
int ferror(FILE *f) {return f?f->err:EOF;}
void rewind(FILE *f) {if(f)fseek(f,0,SEEK_SET);}
int remove(const char *path) {
    char name[64];
    if(!path||normalize_path(path,name,sizeof name)<0) {errno=EINVAL;return -1;}
    int r=bios_call(BIOS_FS_UNLINK,(uintptr_t)name,0,0,0);
    if(r!=0) {errno=ENOENT;return -1;}
    return 0;
}
int rename(const char *from,const char *to) {
    char a[64],b[64];
    if(!from||!to) {errno=EINVAL;return -1;}
    if(normalize_path(from,a,sizeof a)<0 || normalize_path(to,b,sizeof b)<0) {errno=EINVAL;return -1;}
    int r=bios_call(BIOS_FS_RENAME,(uintptr_t)a,(uintptr_t)b,0,0);
    if(r!=0) {errno=EIO;return -1;}
    return 0;
}
int shim_mkdir(const char *path) {
    char name[64];
    if(!path||normalize_path(path,name,sizeof name)<0) {errno=EINVAL;return -1;}
    int r=bios_call(BIOS_FS_MKDIR,(uintptr_t)name,0,0,0);
    if(r!=0) {errno=EACCES;return -1;}
    return 0;
}

/* --------------------------------------------------------------- vsnprintf */
static const char digits_up[]="0123456789ABCDEF";
static const char digits_lo[]="0123456789abcdef";

/* unsigned -> output-ordered text with padding.  prec >= 0 is the C integer
 * precision (minimum digit count, zero padded); when precision is given the
 * "0" flag is ignored, per C.  Returns the field width. */
static unsigned fmt_utoa(char *buf,unsigned cap,unsigned long long v,unsigned base,
                         int upper,int hash,int zero,int width,int minus,int prec) {
    const char *d=upper?digits_up:digits_lo;
    char dig[24];int dc=0;
    if(!v)dig[dc++]='0';
    while(v) {dig[dc++]=(char)d[v%base];v/=base;}
    int prefix=(hash&&base==16)?2:0;
    int zdig=prec>dc?prec-dc:0;
    int pad=width>prefix+zdig+dc?width-(prefix+zdig+dc):0;
    int padzero=zero&&!minus&&prec<0;
    int spaces=(minus||!padzero)?pad:0;
    int zeros=(padzero?pad:0)+zdig;
    char tmp[64];int n=0;
    if(!minus)for(int i=0;i<spaces;++i)tmp[n++]=' ';
    if(prefix) {tmp[n++]='0';tmp[n++]=(char)(upper?'X':'x');}
    for(int i=0;i<zeros;++i)tmp[n++]='0';
    for(int i=dc-1;i>=0;--i)tmp[n++]=dig[i];
    if(minus)for(int i=0;i<spaces;++i)tmp[n++]=' ';
    unsigned w=0;
    for(int i=0;i<n;++i) {if(w<cap)buf[w]=tmp[i];++w;}
    return w;
}

/* double -> fixed decimal. Values are clamped; DOOM only prints small ones. */
static unsigned fmt_double(char *buf,unsigned cap,double v,int prec,int plus,int space) {
    char tmp[48];unsigned w=0;
    if(prec>12)prec=12;
    if(v<0) {tmp[w++]='-';v=-v;} else if(plus)tmp[w++]='+';else if(space)tmp[w++]=' ';
    if(v>1e15) {tmp[w++]='b';tmp[w++]='i';tmp[w++]='g';tmp[w++]='?';}
    else {
        unsigned long long ip=(unsigned long long)v;
        char d[24];int dn=0;
        if(!ip)d[dn++]='0';
        while(ip) {d[dn++]=(char)('0'+ip%10);ip/=10;}
        while(dn)tmp[w++]=d[--dn];
        double frac=v-(double)(unsigned long long)v;
        if(prec>0) {
            tmp[w++]='.';
            for(int i=0;i<prec;++i) {
                frac*=10.0;int dg=(int)frac;
                if(dg>9)dg=9;
                tmp[w++]=(char)('0'+dg);frac-=dg;
            }
        }
    }
    for(unsigned o=0;o<w;++o)if(o<cap)buf[o]=tmp[o];
    return w;
}

int vsnprintf(char *buf,size_t cap,const char *fmt,__builtin_va_list ap) {
    unsigned w=0;
    while(*fmt) {
        if(*fmt!='%') {if(buf&&w<cap)buf[w]=*fmt;++w;++fmt;continue;}
        ++fmt; /* at first char after % */
        int minus=0,plus=0,space=0,hash=0,zero=0,width=0,prec=-1,lcount=0;
        for(;;) {
            char c=*fmt;
            if(c=='-') {minus=1;++fmt;continue;}
            if(c=='+') {plus=1;++fmt;continue;}
            if(c==' ') {space=1;++fmt;continue;}
            if(c=='#') {hash=1;++fmt;continue;}
            if(c=='0') {zero=1;++fmt;continue;}
            if(c>='1'&&c<='9') {width=0;while(*fmt>='0'&&*fmt<='9')width=width*10+*fmt++-'0';continue;}
            if(c=='.') {
                ++fmt;
                if(*fmt=='*') {prec=__builtin_va_arg(ap,int);++fmt;continue;}
                prec=0;while(*fmt>='0'&&*fmt<='9')prec=prec*10+*fmt++-'0';
                continue;
            }
            if(c=='l'||c=='h'||c=='z'||c=='j'||c=='t') {if(c=='l')++lcount;++fmt;continue;}
            break;
        }
        char tmp[512];unsigned tw=0;
        switch(*fmt) {
        case 'd':case 'i': {
            long long v;
            if(!lcount)v=__builtin_va_arg(ap,int);
            else if(lcount==1)v=__builtin_va_arg(ap,long);
            else v=__builtin_va_arg(ap,long long);
            char head[4];int hn=0;
            if(v<0) {head[hn++]='-';v=-v;}
            else if(plus)head[hn++]='+';else if(space)head[hn++]=' ';
            for(int i=0;i<hn;++i) {if(buf&&w<cap)buf[w]=head[i];++w;}
            tw=fmt_utoa(tmp,sizeof tmp,(unsigned long long)v,10,0,0,zero&&prec<0,
                        width>hn?width-hn:0,minus,prec);
            break;}
        case 'u':case 'x':case 'X':case 'o': {
            unsigned long long v;
            if(!lcount)v=__builtin_va_arg(ap,unsigned);
            else if(lcount==1)v=__builtin_va_arg(ap,unsigned long);
            else v=__builtin_va_arg(ap,unsigned long long);
            unsigned base=*fmt=='u'?10:(*fmt=='x'||*fmt=='X')?16:8;
            tw=fmt_utoa(tmp,sizeof tmp,v,base,*fmt=='X',hash,zero&&prec<0,width,minus,prec);
            break;}
        case 'p': {
            void *v=__builtin_va_arg(ap,void*);
            tmp[0]='0';tmp[1]='x';tw=2;
            tw+=fmt_utoa(tmp+2,sizeof tmp-2,(unsigned long long)(uintptr_t)v,16,0,0,0,8,0,-1);
            break;}
        case 'f': {
            double v=__builtin_va_arg(ap,double);
            tw=fmt_double(tmp,sizeof tmp,v,prec<0?6:prec,plus,space);
            break;}
        case 'c': {
            int c=__builtin_va_arg(ap,int);tmp[0]=(char)c;tw=1;break;}
        case 's': {
            const char *s=__builtin_va_arg(ap,const char*);
            if(!s)s="(null)";
            int n=0;while(s[n])++n;
            if(prec>=0&&n>prec)n=prec;
            int pad=width>n?width-n:0;
            if(!minus)for(int i=0;i<pad;++i) {if(buf&&w<cap)buf[w]=' ';++w;}
            for(int i=0;i<n;++i) {if(buf&&w<cap)buf[w]=s[i];++w;}
            if(minus)for(int i=0;i<pad;++i) {if(buf&&w<cap)buf[w]=' ';++w;}
            break;}
        case '%':
            if(buf&&w<cap)buf[w]='%';
            ++w;
            ++fmt;
            continue;
        default:
            if(buf&&w<cap)buf[w]='%';
            ++w;
            if(buf&&w<cap)buf[w]=*fmt;
            ++w;
            ++fmt;
            continue;
        }
        for(unsigned i=0;i<tw;++i) {if(buf&&w<cap)buf[w]=tmp[i];++w;}
        ++fmt;
    }
    if(buf&&cap)buf[w<cap?w:cap-1]=0;
    return (int)w;
}
int snprintf(char *buf,size_t cap,const char *fmt,...) {
    __builtin_va_list ap;__builtin_va_start(ap,fmt);
    int r=vsnprintf(buf,cap,fmt,ap);__builtin_va_end(ap);return r;
}
int vfprintf(FILE *f,const char *fmt,__builtin_va_list ap) {
    char buf[512];
    int n=vsnprintf(buf,sizeof buf,fmt,ap);
    if(!f||f->console)con_write(buf,n<(int)sizeof buf?n:(int)sizeof buf);
    else {f->err=1;errno=EROFS;}
    return n;
}
int fprintf(FILE *f,const char *fmt,...) {
    __builtin_va_list ap;__builtin_va_start(ap,fmt);
    int r=vfprintf(f,fmt,ap);__builtin_va_end(ap);return r;
}
int printf(const char *fmt,...) {
    __builtin_va_list ap;__builtin_va_start(ap,fmt);
    int r=vfprintf(stdout,fmt,ap);__builtin_va_end(ap);return r;
}
int puts(const char *s) {con_write(s,strlen(s));con_write("\r\n",2);return 0;}
int putchar(int c) {char b=(char)c;con_write(&b,1);return c;}
int fputc(int c,FILE *f) {
    if(f&&f->console) {char b=(char)c;con_write(&b,1);return c;}
    return fwrite(&c,1,1,f)?c:EOF;
}
int fputs(const char *s,FILE *f) {
    if(f&&f->console) {con_write(s,strlen(s));return 0;}
    return fwrite(s,1,strlen(s),f)==strlen(s)?0:EOF;
}

/* ------------------------------------------------------------------ sscanf */
int sscanf(const char *in,const char *fmt,...) {
    __builtin_va_list ap;__builtin_va_start(ap,fmt);
    const char *s=in;int assigned=0;
    for(;*fmt;++fmt) {
        if(*fmt!='%') {
            if(*fmt==' ') {while(isspace_(*s))++s;continue;}
            if(*s!=*fmt)break;
            ++s;continue;
        }
        ++fmt;
        if(*fmt=='%') {if(*s!='%')break;++s;continue;}
        int star=0,width=0,lcount=0;
        if(*fmt=='*') {star=1;++fmt;}
        while(*fmt>='0'&&*fmt<='9')width=width*10+*fmt++-'0';
        while(*fmt=='l'||*fmt=='h') {if(*fmt=='l')++lcount;++fmt;}
        if(*fmt=='n') {
            if(!star)*__builtin_va_arg(ap,int*)=(int)(s-in);
            continue;
        }
        if(*fmt=='[')break; /* not supported */
        if(*fmt=='c') {
            int n=width?width:1;
            char *dst=star?0:__builtin_va_arg(ap,char*);
            for(int i=0;i<n;++i) {
                if(!s[i]) {__builtin_va_end(ap);return assigned;}
                if(dst)dst[i]=s[i];
            }
            s+=n;
            if(!star)++assigned;
            continue;
        }
        while(isspace_(*s))++s;
        if(*fmt=='s') {
            char *dst=star?0:__builtin_va_arg(ap,char*);
            int n=0;
            while(*s&&!isspace_(*s)&&(!width||n<width)) {if(dst)dst[n]=*s;++s;++n;}
            if(!n)break;
            if(dst)dst[n]=0;
            if(!star)++assigned;
            continue;
        }
        int base=0;
        if(*fmt=='d')base=10;else if(*fmt=='x'||*fmt=='X')base=16;
        else if(*fmt=='i')base=0;else if(*fmt=='u')base=10;
        else break;
        const char *t=s;int neg=0;
        if(*t=='+'||*t=='-')neg=*t=='-',++t;
        if(!base) {base=10;if(t[0]=='0'&&(t[1]=='x'||t[1]=='X')) {base=16;t+=2;}}
        else if(base==16&&t[0]=='0'&&(t[1]=='x'||t[1]=='X'))t+=2;
        unsigned long long v=0,dig=0;
        while(*t) {
            int d=base==16?hexval(*t):(*t-'0')<10?*t-'0':-1;
            if(d<0||(unsigned)d>=(unsigned)base)break;
            v=v*base+(unsigned)d;++dig;++t;
        }
        if(!dig)break;
        if(neg)v=0u-v;
        if(!star) {
            if(lcount>=2)*__builtin_va_arg(ap,long long*)=(long long)v;
            else if(lcount==1)*__builtin_va_arg(ap,long*)=(long)v;
            else *__builtin_va_arg(ap,int*)=(int)v;
            ++assigned;
        }
        s=t;
    }
    __builtin_va_end(ap);
    return assigned;
}

/* -------------------------------------------------------------------- heap
 * Block layout: [size:4][payload...]; size includes the 4-byte header and a
 * 4-byte free-list link that lives at the payload start while free. Free list
 * is singly linked from free_head; no coalescing (single process, grow-mostly
 * allocation pattern; DOOM frees rarely and reallocs lumpinfo only). */
#define HEAP_BYTES (48u<<20)
static unsigned long heap_store[HEAP_BYTES/sizeof(unsigned long)] __attribute__((aligned(8)));
static unsigned char *const heap_lo=(unsigned char*)heap_store;
static unsigned char *const heap_hi=(unsigned char*)heap_store+HEAP_BYTES;
static unsigned char *free_head;

#define BLK_HDR 8 /* size word + link slot */
static unsigned long blk_size(unsigned char *p) {return *(unsigned long*)(p-4)&~1ul;}
static int blk_free(unsigned char *p) {return (int)(*(unsigned long*)(p-4)&1);}
static void blk_set(unsigned char *p,unsigned long sz,int free_) {
    *(unsigned long*)(p-4)=sz|(unsigned)free_;
}
static void heap_init(void) {
    if(free_head)return;
    unsigned char *p=heap_lo+BLK_HDR; /* first size word at heap_lo+4 */
    free_head=p;
    blk_set(p,(unsigned long)(heap_hi-BLK_HDR-p),1);
    *(unsigned long*)p=0;
}
void *malloc(size_t want) {
    heap_init();
    if(!want)++want;
    want=(want+7)&~(size_t)7;
    if(want+BLK_HDR>(size_t)(heap_hi-heap_lo))return 0;
    unsigned char *prev=0,*p=free_head;
    while(p) {
        unsigned long sz=blk_size(p);
        if(sz>=want+BLK_HDR) {
            /* unlink p from the free list */
            unsigned long next=*(unsigned long*)p;
            if(prev)*(unsigned long*)prev=next;else free_head=(unsigned char*)(uintptr_t)next;
            if(sz>=want+BLK_HDR+16) { /* split */
                unsigned char *rest=p+want+BLK_HDR;
                blk_set(rest,sz-want-BLK_HDR,1);
                *(unsigned long*)rest=(uintptr_t)free_head;
                free_head=rest;
                blk_set(p,want+BLK_HDR,0);
            } else blk_set(p,sz,0);
            return p;
        }
        prev=p;p=(unsigned char*)(uintptr_t)*(unsigned long*)p;
    }
    return 0;
}
void free(void *v) {
    if(!v)return;
    unsigned char *p=v;
    if(p<heap_lo+BLK_HDR||p>=heap_hi)return; /* foreign: ignore */
    if(blk_free(p))return; /* double free: ignore */
    blk_set(p,blk_size(p),1);
    *(unsigned long*)p=(uintptr_t)free_head;
    free_head=p;
}
void *calloc(size_t n,size_t sz) {
    size_t t=n*sz;void *p=malloc(t);
    if(p)memset(p,0,t);
    return p;
}
void *realloc(void *old,size_t want) {
    if(!old)return malloc(want);
    if(!want) {free(old);return 0;}
    unsigned char *p=old;
    unsigned long have=blk_size(p)-BLK_HDR;
    if(have>=want)return old;
    void *np=malloc(want);
    if(!np)return 0;
    memcpy(np,old,have<want?have:want);
    free(old);
    return np;
}

/* -------------------------------------------------------------- str extras */
void *memmove(void *d,const void *s,size_t n) {
    unsigned char *dp=d;const unsigned char *sp=s;
    if(dp<sp+n&&sp<dp+n) {dp+=n;sp+=n;while(n--)*--dp=*--sp;}
    else memcpy(d,s,n);
    return d;
}
void *memchr(const void *v,int c,size_t n) {
    const unsigned char *p=v;for(;n;--n,++p)if(*p==(unsigned char)c)return (void*)p;
    return 0;
}
char *strcpy(char *d,const char *s) {char *r=d;while((*d++=*s++));return r;}
char *strncpy(char *d,const char *s,size_t n) {
    char *r=d;size_t i=0;
    for(;i<n&&s[i];++i)d[i]=s[i];
    for(;i<n;++i)d[i]=0;
    return r;
}
char *strcat(char *d,const char *s) {char *r=d;while(*d)++d;while((*d++=*s++));return r;}
char *strncat(char *d,const char *s,size_t n) {
    char *r=d;while(*d)++d;
    for(size_t i=0;i<n&&*s;++i)*d++=*s++;
    *d=0;return r;
}
char *strrchr(const char *s,int c) {
    const char *r=0;
    for(;*s;++s)if(*s==(char)c)r=s;
    return (char*)r;
}
char *strstr(const char *h,const char *n) {
    size_t l=strlen(n);
    if(!l)return (char*)h;
    for(;*h;++h)if(!strncmp(h,n,l))return (char*)h;
    return 0;
}
char *strpbrk(const char *s,const char *b) {
    for(;*s;++s)for(const char *q=b;*q;++q)if(*s==*q)return (char*)s;
    return 0;
}
size_t strspn(const char *s,const char *b) {
    size_t n=0;
    for(;*s;++s) {
        const char *q=b;
        for(;*q;++q)if(*s==*q)break;
        if(!*q)return n;
        ++n;
    }
    return n;
}
size_t strcspn(const char *s,const char *b) {
    size_t n=0;
    for(;*s;++s) {
        for(const char *q=b;*q;++q)if(*s==*q)return n;
        ++n;
    }
    return n;
}
char *strdup(const char *s) {
    size_t n=strlen(s)+1;char *d=malloc(n);
    if(d)memcpy(d,s,n);
    return d;
}
int strcasecmp(const char *a,const char *b) {
    for(;;) {
        int ca=tolower_(a[0]),cb=tolower_(b[0]);
        if(ca!=cb)return ca-cb;
        if(!ca)return 0;
        ++a;++b;
    }
}
int strncasecmp(const char *a,const char *b,size_t n) {
    for(;n;--n,++a,++b) {
        int ca=tolower_(a[0]),cb=tolower_(b[0]);
        if(ca!=cb)return ca-cb;
        if(!ca)return 0;
    }
    return 0;
}
unsigned long strtoul(const char *s,char **end,int base) {
    while(isspace_(*s))++s;
    int neg=0;
    if(*s=='-') {neg=1;++s;}else if(*s=='+')++s;
    if(!base) {base=10;if(s[0]=='0'&&(s[1]=='x'||s[1]=='X'))base=16;}
    else if(base==16&&s[0]=='0'&&(s[1]=='x'||s[1]=='X'))s+=2;
    unsigned long v=0;
    for(;;++s) {
        int d=base==16?hexval(*s):(*s>='0'&&*s<'0'+base)?*s-'0':-1;
        if(d<0)break;
        v=v*base+(unsigned)d;
    }
    if(end)*end=(char*)s;
    return neg?0UL-v:v;
}
long strtol(const char *s,char **e,int base) {return (long)strtoul(s,e,base);}
int atoi(const char *s) {return (int)strtol(s,0,10);}
long atol(const char *s) {return strtol(s,0,10);}
double atof(const char *s) {
    while(isspace_(*s))++s;
    int neg=0;
    if(*s=='-') {neg=1;++s;}else if(*s=='+')++s;
    double v=0;
    while(*s>='0'&&*s<='9')v=v*10+(*s++-'0');
    if(*s=='.') {
        double sc=0.1;++s;
        while(*s>='0'&&*s<='9') {v+=(*s++-'0')*sc;sc*=0.1;}
    }
    if(*s=='e'||*s=='E') {
        ++s;int en=0,eneg=0;
        if(*s=='-') {eneg=1;++s;}else if(*s=='+')++s;
        while(*s>='0'&&*s<='9')en=en*10+(*s++-'0');
        double p=1;while(en--)p*=10;
        v=eneg?v/p:v*p;
    }
    return neg?-v:v;
}
char *getenv(const char *name) {(void)name;return 0;}

/* ------------------------------------------------------------------- exit */
static uint32_t exit_buf[16];
static int exit_armed;
int shim_setjmp(void) {
    extern int shim_setjmp_asm(uint32_t*);
    int r=shim_setjmp_asm(exit_buf);
    if(!r)exit_armed=1;
    return r;
}
int shim_exit_armed(void) {return exit_armed;}
void exit(int code) {
    extern void shim_longjmp_asm(const uint32_t*,int);
    if(exit_armed) {
        exit_armed=0;
        shim_longjmp_asm(exit_buf,code?code:1);
    }
    {static const char msg[]="EXIT (not armed)\r\n";con_write(msg,sizeof msg-1);}
    for(;;)bios_call(BIOS_POLL,0,0,0,0);
}
void abort(void) {exit(134);}

/* ------------------------------------------------------------------- math */
double fabs(double x) {return x<0?-x:x;}
double floor(double x) {
    long long i=(long long)x;
    if((double)i>x)--i;
    return (double)i;
}
double ceil(double x) {
    long long i=(long long)x;
    if((double)i<x)++i;
    return (double)i;
}
double fmod(double a,double b) {
    if(!b)return a;
    long long i=(long long)(a/b);
    return a-(double)i*b;
}
double sqrt(double x) {
    if(x<=0)return 0;
    double g=x;
    for(int i=0;i<40;++i)g=(g+x/g)*0.5;
    return g;
}

/* --------------------------------------------------------------- asm jmp */
__asm__(
".text\n.align 2\n"
".globl shim_setjmp_asm\n.type shim_setjmp_asm,@function\n"
"shim_setjmp_asm:\n"
" sw ra,0(a0)\n sw sp,4(a0)\n sw s0,8(a0)\n sw s1,12(a0)\n"
" sw s2,16(a0)\n sw s3,20(a0)\n sw s4,24(a0)\n sw s5,28(a0)\n"
" sw s6,32(a0)\n sw s7,36(a0)\n sw s8,40(a0)\n sw s9,44(a0)\n"
" sw s10,48(a0)\n sw s11,52(a0)\n"
" li a0,0\n ret\n"
".size shim_setjmp_asm,.-shim_setjmp_asm\n"
".globl shim_longjmp_asm\n.type shim_longjmp_asm,@function\n"
"shim_longjmp_asm:\n"
" lw ra,0(a0)\n lw sp,4(a0)\n lw s0,8(a0)\n lw s1,12(a0)\n"
" lw s2,16(a0)\n lw s3,20(a0)\n lw s4,24(a0)\n lw s5,28(a0)\n"
" lw s6,32(a0)\n lw s7,36(a0)\n lw s8,40(a0)\n lw s9,44(a0)\n"
" lw s10,48(a0)\n lw s11,52(a0)\n"
" mv a0,a1\n bnez a1,1f\n li a0,1\n"
"1: ret\n"
".size shim_longjmp_asm,.-shim_longjmp_asm\n"
);
