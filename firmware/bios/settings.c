#include "internal.h"
#include "ff.h"
#include <string.h>
struct bios_settings bios_settings;
#if MINI_FEATURE_FILESYSTEM
static int valid(const struct bios_settings *s) {
    unsigned n=0;while(n<sizeof(s->file) && s->file[n])++n;
    return s->magic==BIOS_ECALL_MAGIC && s->version==1 && s->boot<=2 && s->delay_ms<=30000 &&
        s->file[0] && n<sizeof(s->file);
}
#endif
void bios_settings_default(void) {
    memset(&bios_settings,0,sizeof(bios_settings));bios_settings.magic=BIOS_ECALL_MAGIC;bios_settings.version=1;
    bios_settings.delay_ms=3000;bios_settings.ip[0]=169;bios_settings.ip[1]=254;bios_settings.ip[2]=20;bios_settings.ip[3]=20;
    bios_settings.server[0]=169;bios_settings.server[1]=254;bios_settings.server[2]=20;bios_settings.server[3]=1;
    memcpy(bios_settings.file,"BOOT.RPB",9);
}
int bios_settings_load(void) {
#if MINI_FEATURE_FILESYSTEM
    FIL f;UINT count;struct bios_settings s;
    if(hal_sd_mount()!=HAL_OK || f_open(&f,"BIOS.CFG",FA_READ)!=FR_OK)return 0;
    FRESULT r=f_read(&f,&s,sizeof(s),&count);unsigned size=f_size(&f);f_close(&f);
    if(r!=FR_OK || count!=sizeof(s) || size!=sizeof(s) || !valid(&s))return 0;
    bios_settings=s;return 1;
#else
    return 0;
#endif
}
int bios_settings_save(void) {
#if MINI_FEATURE_FILESYSTEM
    FIL f;UINT count;if(!valid(&bios_settings) || hal_sd_mount()!=HAL_OK || f_open(&f,"BIOS.CFG",FA_WRITE|FA_CREATE_ALWAYS)!=FR_OK)return 0;
    FRESULT r=f_write(&f,&bios_settings,sizeof(bios_settings),&count);FRESULT c=f_close(&f);
    return r==FR_OK && c==FR_OK && count==sizeof(bios_settings);
#else
    return 0;
#endif
}
