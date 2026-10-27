#include "ae350_bios.h"
#include "ae350_sd.h"
#include "ff.h"
#include <string.h>
extern int payload_main(const struct bios_info *info);
static void decimal(unsigned value) {char s[12];unsigned n=0;do {s[n++]=(char)('0'+value%10);value/=10;}while(value);while(n)bios_putc(s[--n]);}
void bios_command(char *line) {
    if(!strcmp(line,"help")) {bios_puts("help, status, ls [DIR], cat FILE, size FILE, mkdir DIR, rm FILE, mv FROM TO, unmount, basic\r\n");return;}
    if(!strcmp(line,"basic")) {payload_main(&ae350_bios_info);bios_puts("APPLICATION RETURNED\r\n");return;}
    if(!strcmp(line,"status")) {
        struct ae350_sd_info sd;ae350_sd_info(&sd);
        bios_puts("AE350 CPU800/sys50 SD=");decimal(sd.hz);bios_puts(" Hz BIOS ABI=1\r\n");return;
    }
    if(!strcmp(line,"unmount")) {bios_puts(bios_unmount()?"UNMOUNT FAIL\r\n":"UNMOUNT OK\r\n");return;}
    if(bios_mount()) {bios_puts("SD unavailable\r\n");return;}
    if(!strcmp(line,"ls") || !strncmp(line,"ls ",3)) {
        DIR dir;FILINFO entry;
        FRESULT r=f_opendir(&dir,line[2]?line+3:"/");
        if(r==FR_OK) {
            for(;;) {
                r=f_readdir(&dir,&entry);if(r!=FR_OK || !entry.fname[0])break;
                bios_puts(entry.fname);if(entry.fattrib&AM_DIR)bios_putc('/');bios_puts("\r\n");bios_poll();
            }
            FRESULT end=f_closedir(&dir);if(!r)r=end;
        }
        bios_puts(r==FR_OK?"LS OK\r\n":"LS FAIL\r\n");return;
    }
    if(!strncmp(line,"cat ",4)) {
        int h=bios_fs_open(line+4,BIOS_FS_O_READ);char data[256];int n=-1;
        if(h>=0) {
            unsigned shown=0;
            while(shown<4096 && (n=bios_fs_read(h,data,sizeof(data)))>0) {
                for(int i=0;i<n;i++)bios_putc(data[i]);
                shown+=(unsigned)n;bios_poll();
            }
            if(bios_fs_close(h))n=-1;
        }
        bios_puts(n<0?"\r\nCAT FAIL\r\n":"\r\nCAT OK\r\n");return;
    }
    if(!strncmp(line,"size ",5)) {
        int n=bios_file_size(line+5);if(n<0)bios_puts("SIZE FAIL");else {bios_puts("SIZE ");decimal((unsigned)n);}bios_puts("\r\n");return;
    }
    FRESULT r=FR_INVALID_PARAMETER;
    if(!strncmp(line,"mkdir ",6))r=f_mkdir(line+6);
    else if(!strncmp(line,"rm ",3))r=f_unlink(line+3);
    else if(!strncmp(line,"mv ",3)) {char *to=strchr(line+3,' ');if(to) {*to++=0;r=f_rename(line+3,to);}}
    else {bios_puts("Unknown command\r\n");return;}
    bios_puts(r==FR_OK?"FILE OK\r\n":"FILE FAIL\r\n");
}
void main(void) {
    bios_runtime_init();bios_puts("AE350 BIOS READY\r\n");bios_editor();
}
