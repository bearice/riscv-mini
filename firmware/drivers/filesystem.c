#include "io.h"
#include "ff.h"
#include <string.h>

static FATFS fs;
static unsigned mounted;
static unsigned char buffer[512];
static uint32_t crc32(uint32_t crc, const unsigned char *p, unsigned len) {
    for (unsigned i=0; i<len; ++i) {
        crc^=p[i];
        for (unsigned bit=0; bit<8; ++bit) crc=(crc>>1)^((crc&1)?0xedb88320u:0);
    }
    return crc;
}
static int error(const char *operation, FRESULT result) {
    puts_uart("SD "); puts_uart(operation); puts_uart(" failed FatFs="); io_hex(result); puts_uart("\r\n");
    return 0;
}
int sd_mount_info(void) {
    mounted=0;
    f_mount(0,"",0);
    FRESULT r=f_mount(&fs,"",1);
    if (r!=FR_OK) return error("mount",r);
    mounted=1;
    puts_uart("SD filesystem mounted: type="); io_hex(fs.fs_type); puts_uart("\r\n");
    DIR dir; FILINFO info;
    r=f_opendir(&dir,"/");
    if (r!=FR_OK) return error("opendir",r);
    for (unsigned i=0; i<16; ++i) {
        r=f_readdir(&dir,&info);
        if (r!=FR_OK || !info.fname[0]) break;
        puts_uart("SD entry: "); puts_uart(info.fname); puts_uart("\r\n");
    }
    f_closedir(&dir);
    return r==FR_OK ? 1 : error("readdir",r);
}
static unsigned char pattern(unsigned offset) { return (offset*73u)^(offset>>3)^0x5au; }
int sd_file_test(void) {
    /* Remount first: supports reinsertion and avoids retaining stale state. */
    if (!sd_mount_info()) return 0;
    FIL file;
    char name[]="RVTEST00.BIN";
    FRESULT r=FR_EXIST;
    for (unsigned i=0; i<100; ++i) {
        name[6]='0'+i/10; name[7]='0'+i%10;
        r=f_open(&file,name,FA_WRITE|FA_CREATE_NEW);
        if (r!=FR_EXIST) break;
    }
    if (r!=FR_OK) return error("create-new",r);
    uint32_t written_crc=0xffffffffu, read_crc=0xffffffffu;
    for (unsigned block=0; block<8; ++block) {
        for (unsigned i=0; i<512; ++i) buffer[i]=pattern(block*512+i);
        UINT count=0;
        r=f_write(&file,buffer,512,&count);
        if (r!=FR_OK || count!=512) { f_close(&file); return error("write",r==FR_OK ? FR_DISK_ERR : r); }
        written_crc=crc32(written_crc,buffer,512);
    }
    r=f_close(&file);
    if (r!=FR_OK) return error("close-write",r);
    r=f_open(&file,name,FA_READ);
    if (r!=FR_OK) return error("open-read",r);
    if (f_size(&file)!=4096) { f_close(&file); return error("file-size",FR_INT_ERR); }
    for (unsigned block=0; block<8; ++block) {
        UINT count=0;
        r=f_read(&file,buffer,512,&count);
        if (r!=FR_OK || count!=512) { f_close(&file); return error("read",r==FR_OK ? FR_DISK_ERR : r); }
        for (unsigned i=0; i<512; ++i)
            if (buffer[i]!=pattern(block*512+i)) { f_close(&file); return error("compare",FR_INT_ERR); }
        read_crc=crc32(read_crc,buffer,512);
    }
    r=f_close(&file);
    if (r!=FR_OK) return error("close-read",r);
    if (written_crc!=read_crc) return error("CRC32",FR_INT_ERR);
    puts_uart("SD FILE PASS: "); puts_uart(name); puts_uart(" bytes=4096 CRC32=");
    io_hex(read_crc^0xffffffffu); puts_uart(" wire CRC16 checked\r\n");
    lcd_show(1,1);
    return 1;
}
static int sd_read_test(const char *name) {
    if (!sd_mount_info()) return 0;
    FIL file;
    FRESULT r=f_open(&file,name,FA_READ);
    if (r!=FR_OK) return error("open-check",r);
    if (f_size(&file)!=4096) { f_close(&file); return error("check-size",FR_INT_ERR); }
    uint32_t crc=0xffffffffu;
    for (unsigned block=0; block<8; ++block) {
        UINT count=0;
        r=f_read(&file,buffer,512,&count);
        if (r!=FR_OK || count!=512) { f_close(&file); return error("check-read",r==FR_OK ? FR_DISK_ERR : r); }
        for (unsigned i=0; i<512; ++i)
            if (buffer[i]!=pattern(block*512+i)) { f_close(&file); return error("check-compare",FR_INT_ERR); }
        crc=crc32(crc,buffer,512);
    }
    r=f_close(&file);
    if (r!=FR_OK) return error("check-close",r);
    puts_uart("SD READ PASS: "); puts_uart(name); puts_uart(" bytes=4096 CRC32=");
    io_hex(crc^0xffffffffu); puts_uart("\r\n");
    return 1;
}
int sd_command(const char *line) {
    if (!strcmp(line,"sdinfo")) { int ok=sd_mount_info(); lcd_show(ok,0); return 1; }
    if (!strcmp(line,"sdtest")) { if (!sd_file_test()) puts_uart("SD FILE FAIL\r\n"); return 1; }
    if (strlen(line)>8 && !memcmp(line,"sdcheck ",8)) {
        if (!sd_read_test(line+8)) puts_uart("SD READ FAIL\r\n");
        return 1;
    }
    if (!strcmp(line,"lcd")) { lcd_show(mounted,0); return 1; }
    return 0;
}
