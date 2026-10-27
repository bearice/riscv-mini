#include "ae350_bios.h"
#include "ae350_sd.h"
#include "ff.h"
#include <limits.h>
static FATFS volume;
static unsigned mounted;
static struct {FIL file;unsigned used,writable;} handles[6];
int bios_mount(void) {
    if(mounted && !ae350_sd_status())return 0;
    for(unsigned i=0;i<6;i++)if(handles[i].used)return -1;
    mounted=0;f_mount(0,"",0);
    if(f_mount(&volume,"",1)!=FR_OK)return -1;
    mounted=1;return 0;
}
int bios_unmount(void) {
    for(unsigned i=0;i<6;i++)if(handles[i].used)return -1;
    if(mounted && ae350_sd_sync())return -1;
    if(f_mount(0,"",0)!=FR_OK)return -1;
    mounted=0;return ae350_sd_stop();
}
static int valid(int h) {return h>=0 && h<6 && handles[h].used;}
int bios_fs_open(const char *path,unsigned flags) {
    if(!path || !flags || (flags&~31u) || !(flags&3u) ||
       ((flags&28u) && !(flags&BIOS_FS_O_WRITE)) || bios_mount())return -1;
    BYTE mode=(flags&BIOS_FS_O_READ?FA_READ:0)|(flags&BIOS_FS_O_WRITE?FA_WRITE:0);
    if(flags&BIOS_FS_O_TRUNC)mode|=FA_CREATE_ALWAYS;
    else if(flags&BIOS_FS_O_CREATE)mode|=FA_CREATE_NEW;
    else if(flags&BIOS_FS_O_APPEND)mode|=FA_OPEN_APPEND;
    for(int h=0;h<6;h++)if(!handles[h].used) {
        if(f_open(&handles[h].file,path,mode)!=FR_OK)return -1;
        handles[h].used=1;handles[h].writable=!!(flags&BIOS_FS_O_WRITE);return h;
    }
    return -1;
}
int bios_fs_close(int h) {
    if(!valid(h))return -1;
    if(f_close(&handles[h].file)!=FR_OK)return -1;
    handles[h].used=0;return 0;
}
int bios_fs_read(int h,void *data,unsigned bytes) {
    if(!valid(h) || (!data && bytes) || bytes>INT_MAX)return -1;
    UINT n;return f_read(&handles[h].file,data,bytes,&n)==FR_OK?(int)n:-1;
}
int bios_fs_write(int h,const void *data,unsigned bytes) {
    if(!valid(h) || !handles[h].writable || (!data && bytes) || bytes>INT_MAX)return -1;
    UINT n;return f_write(&handles[h].file,data,bytes,&n)==FR_OK?(int)n:-1;
}
int bios_fs_seek(int h,int offset,int whence) {
    if(!valid(h))return -1;
    FIL *f=&handles[h].file;
    int64_t base=whence==BIOS_FS_SET?0:whence==BIOS_FS_CUR?(int64_t)f_tell(f):whence==BIOS_FS_END?(int64_t)f_size(f):-1;
    int64_t target=base+offset;
    if(base<0 || target<0 || target>INT_MAX || f_lseek(f,(FSIZE_t)target)!=FR_OK)return -1;
    return (int)f_tell(f);
}
int bios_fs_stat(const char *path,struct bios_fs_stat *out) {
    FILINFO st;
    if(!out || bios_mount() || f_stat(path,&st)!=FR_OK || st.fsize>INT_MAX)return -1;
    out->size=(uint32_t)st.fsize;out->attrib=st.fattrib;return 0;
}
int bios_file_read(const char *path,unsigned offset,void *data,unsigned bytes) {
    if(offset>INT_MAX)return -1;
    int h=bios_fs_open(path,BIOS_FS_O_READ);if(h<0)return -1;
    int n=bios_fs_seek(h,(int)offset,BIOS_FS_SET)<0?-1:bios_fs_read(h,data,bytes);
    return bios_fs_close(h)?-1:n;
}
int bios_file_size(const char *path) {struct bios_fs_stat st;return bios_fs_stat(path,&st)?-1:(int)st.size;}
