#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "ae350_bios.h"
#include "ae350_sd.h"
#include "ff.h"
#include "diskio.h"
static uint8_t medium[16u<<20],input[8194],output[8194];
static unsigned ready,fail_sync,fail_stop;
int ae350_sd_init(unsigned channel) {assert(channel==4);ready=1;return 0;}
int ae350_sd_status(void) {return ready?0:-3;}
int ae350_sd_sync(void) {return fail_sync?-1:0;}
int ae350_sd_stop(void) {if(fail_stop)return 9;ready=0;return 0;}
void ae350_sd_info(struct ae350_sd_info *p) {memset(p,0,sizeof(*p));p->sectors=32768;p->hz=25000000;p->width=4;}
int ae350_sd_read(uint32_t sector,void *p,unsigned n) {
    if(!n || sector>=32768 || n>32768-sector)return -2;
    memcpy(p,medium+sector*512,n*512);return 0;
}
int ae350_sd_write(uint32_t sector,const void *p,unsigned n) {
    if(!n || sector>=32768 || n>32768-sector)return -2;
    memcpy(medium+sector*512,p,n*512);return 0;
}
static void word(unsigned off,unsigned n) {medium[off]=(uint8_t)n;medium[off+1]=(uint8_t)(n>>8);}
int main(void) {
    /* FAT16 superfloppy: 32768 sectors, two FATs, 512 root entries. */
    medium[0]=0xeb;medium[1]=0x3c;medium[2]=0x90;
    memcpy(medium+3,"P7TEST  ",8);word(11,512);medium[13]=1;word(14,1);
    medium[16]=2;word(17,512);word(19,32768);medium[21]=0xf8;word(22,128);
    medium[38]=0x29;memcpy(medium+54,"FAT16   ",8);word(510,0xaa55);
    for(unsigned i=0;i<2;i++) {unsigned start=(1+i*128)*512;word(start,0xfff8);word(start+2,0xffff);}
    assert(!bios_mount());assert(disk_read(1,output,0,1)==RES_PARERR);
    assert(disk_read(0,output,32768,1)==RES_PARERR);
    assert(bios_fs_open("/missing",BIOS_FS_O_READ)==-1);
    assert(bios_fs_open("/bad",BIOS_FS_O_TRUNC)==-1);
    assert(bios_fs_open("/bad",32)==-1);
    assert(f_mkdir("/acceptance")==FR_OK);
    int h=bios_fs_open("/acceptance/pattern.bin",BIOS_FS_O_WRITE|BIOS_FS_O_CREATE);
    assert(h>=0);assert(bios_unmount()==-1);
    for(unsigned i=0;i<8192;i++)input[i+1]=(uint8_t)(i*37u^(i>>8));
    input[0]=input[8193]=0xa5;
    assert(bios_fs_write(h,input+1,8192)==8192);assert(!bios_fs_close(h));
    assert(input[0]==0xa5 && input[8193]==0xa5);
    assert(bios_fs_open("/acceptance/pattern.bin",BIOS_FS_O_WRITE|BIOS_FS_O_CREATE)==-1);
    assert(!bios_unmount());assert(!bios_mount());
    struct bios_fs_stat st;assert(!bios_fs_stat("/acceptance/pattern.bin",&st));assert(st.size==8192);
    h=bios_fs_open("/acceptance/pattern.bin",BIOS_FS_O_READ);assert(h>=0);
    assert(bios_fs_write(h,input,1)==-1);
    output[0]=output[8193]=0xa5;
    assert(bios_fs_read(h,output+1,8192)==8192);assert(!memcmp(input,output,sizeof(input)));
    assert(bios_fs_seek(h,-1,BIOS_FS_SET)==-1);
    assert(bios_fs_seek(h,(-2147483647-1),BIOS_FS_CUR)==-1);
    assert(bios_fs_seek(h,-17,BIOS_FS_END)==8175);
    assert(bios_fs_read(h,output,17)==17);assert(!memcmp(output,input+8176,17));
    assert(!bios_fs_close(h));assert(bios_fs_close(h)==-1);
    int handles[6];for(unsigned i=0;i<6;i++) {handles[i]=bios_fs_open("/acceptance/pattern.bin",BIOS_FS_O_READ);assert(handles[i]>=0);}
    assert(bios_fs_open("/acceptance/pattern.bin",BIOS_FS_O_READ)==-1);
    for(unsigned i=0;i<6;i++)assert(!bios_fs_close(handles[i]));
    fail_sync=1;assert(bios_unmount()==-1);fail_sync=0;
    assert(!bios_unmount());assert(!bios_mount());
    assert(f_rename("/acceptance/pattern.bin","/acceptance/moved.bin")==FR_OK);
    assert(f_unlink("/acceptance/moved.bin")==FR_OK);assert(f_unlink("/acceptance")==FR_OK);
    fail_stop=1;assert(bios_unmount()==9);fail_stop=0;
    assert(!bios_unmount());puts("PASS native FatFs disk adapter, persistence, guards, handles, flags, seek and unmount ownership");
}
