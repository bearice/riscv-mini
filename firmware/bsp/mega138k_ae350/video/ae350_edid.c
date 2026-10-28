#include "ae350_edid.h"
#include "board_management.h"
#include "ae350_video.h"

int ae350_edid_read_block(unsigned block,uint8_t buffer[128]) {
    static const uint8_t header[8]={0,255,255,255,255,255,255,0};
    if(!buffer || block>255)return BUS_INVALID;
    unsigned connected=0;
    if(ae350_video_get_hpd(&connected))return BUS_DEVICE;
    if(!connected)return AE350_EDID_DISCONNECTED;
    int rc=board_hdmi_edid_read_block(block,buffer);
    if(rc)return rc;
    if(ae350_video_get_hpd(&connected))return BUS_DEVICE;
    if(!connected)return AE350_EDID_DISCONNECTED;
    if(!block)for(unsigned i=0;i<8;i++)
        if(buffer[i]!=header[i])return AE350_EDID_HEADER;
    unsigned sum=0;
    for(unsigned i=0;i<128;i++)sum+=buffer[i];
    return (sum&255u)?AE350_EDID_CHECKSUM:0;
}
int ae350_edid_read(void *buffer,unsigned capacity,unsigned *blocks_read) {
    if(!blocks_read)return BUS_INVALID;
    *blocks_read=0;
    if(!buffer || capacity<128)return BUS_INVALID;
    uint8_t *out=buffer;
    int rc=ae350_edid_read_block(0,out);
    if(rc)return rc;
    *blocks_read=1;
    unsigned count=1u+out[126];
    if(capacity/128u<count)return AE350_EDID_CAPACITY;
    for(unsigned block=1;block<count;block++) {
        rc=ae350_edid_read_block(block,out+block*128u);
        if(rc)return rc;
        *blocks_read=block+1;
    }
    return 0;
}
