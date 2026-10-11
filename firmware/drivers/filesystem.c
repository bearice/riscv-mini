#include "io.h"
#include "ff.h"
#include "diskio.h"   /* disk_status(): cheap readiness check for the mount cache */
static FATFS filesystem;
static int mounted;
/* Mount once and keep it. f_mount(...,1) forces disk_initialize() on the next
 * access, so calling this before every file operation re-initialised the card
 * each time: ~12 ms of CMD0/CMD8/ACMD41/CMD9/ACMD6 per call, which is invisible
 * for one shell command but dominates a payload that reads a file in 4 KiB
 * chunks (the BIOS log filled with one "SD init PASS" line per read). The card
 * is re-initialised when it was absent or when the driver lost its state
 * (disk_status() reports STA_NOINIT after a media change or a transfer error). */
int sd_mount(void) {
    if(mounted && disk_status(0)==0) return 1;
    mounted=0;
    f_mount(0,"",0);
    FRESULT result=f_mount(&filesystem,"",1);
    if(result!=FR_OK) {puts_uart("SD mount failed FatFs=");io_hex(result);puts_uart("\r\n");return 0;}
    mounted=1;
    return 1;
}
int sd_list(void) {
    if(!sd_mount()) return 0;
    DIR directory;FILINFO info;
    FRESULT result=f_opendir(&directory,"/");if(result!=FR_OK) return 0;
    for(unsigned i=0;i<64;++i) {
        result=f_readdir(&directory,&info);if(result!=FR_OK || !info.fname[0]) break;
        puts_uart(info.fname);puts_uart("\r\n");
    }
    f_closedir(&directory);return result==FR_OK;
}
