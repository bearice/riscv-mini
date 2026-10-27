/* FatFs physical drive 0. SD owns hard-DMAC channel 4 until unmount/stop. */
#include "ff.h"
#include "diskio.h"
#include "ae350_sd.h"
DSTATUS disk_status(BYTE drive) {return drive || ae350_sd_status()?STA_NOINIT:0;}
DSTATUS disk_initialize(BYTE drive) {
    if(drive)return STA_NOINIT;
    return !ae350_sd_status() || !ae350_sd_init(4)?0:STA_NOINIT;
}
DRESULT disk_read(BYTE drive,BYTE *buffer,LBA_t sector,UINT count) {
    if(drive || !buffer || !count)return RES_PARERR;
    if(disk_status(drive))return RES_NOTRDY;
    int rc=ae350_sd_read(sector,buffer,count);
    return rc==-2?RES_PARERR:rc?RES_ERROR:RES_OK;
}
DRESULT disk_write(BYTE drive,const BYTE *buffer,LBA_t sector,UINT count) {
    if(drive || !buffer || !count)return RES_PARERR;
    if(disk_status(drive))return RES_NOTRDY;
    int rc=ae350_sd_write(sector,buffer,count);
    return rc==-2?RES_PARERR:rc?RES_ERROR:RES_OK;
}
DRESULT disk_ioctl(BYTE drive,BYTE command,void *buffer) {
    if(drive)return RES_PARERR;
    if(disk_status(drive))return RES_NOTRDY;
    if(command==CTRL_SYNC)return ae350_sd_sync()?RES_ERROR:RES_OK;
    if(!buffer)return RES_PARERR;
    struct ae350_sd_info info;ae350_sd_info(&info);
    if(command==GET_SECTOR_COUNT)*(LBA_t *)buffer=info.sectors;
    else if(command==GET_SECTOR_SIZE)*(WORD *)buffer=512;
    else if(command==GET_BLOCK_SIZE)*(DWORD *)buffer=1;
    else return RES_PARERR;
    return RES_OK;
}
