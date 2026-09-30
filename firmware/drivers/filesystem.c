#include "io.h"
#include "ff.h"
static FATFS filesystem;
int sd_mount(void) {
    f_mount(0,"",0);FRESULT result=f_mount(&filesystem,"",1);
    if(result!=FR_OK) {puts_uart("SD mount failed FatFs=");io_hex(result);puts_uart("\r\n");return 0;}
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
