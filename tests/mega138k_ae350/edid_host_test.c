#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "ae350_edid.h"
#include "board_management.h"
static unsigned connected=1,hpd_error,unplug;
int ae350_video_get_hpd(unsigned *value) {*value=connected;return (int)hpd_error;}
static unsigned calls,extensions=3,bad_header,bad_checksum,fail_block=256;
int board_hdmi_edid_read_block(unsigned block,void *buffer) {
    assert(block==calls++);
    if(unplug)connected=0;
    if(block==fail_block)return BUS_NACK;
    uint8_t *b=buffer;memset(b,0,128);
    if(!block) {
        memset(b+1,255,6);b[126]=(uint8_t)extensions;
        if(bad_header)b[0]=1;
    } else b[0]=2;
    unsigned sum=0;for(unsigned i=0;i<127;i++)sum+=b[i];
    b[127]=(uint8_t)(0u-sum);
    if(bad_checksum && block==1)b[127]^=1;
    return 0;
}
int main(void) {
    uint8_t b[32770];unsigned count=999;
    memset(b,0xa5,sizeof(b));
    assert(!ae350_edid_read(b+1,32768,&count));
    assert(count==4 && calls==4 && b[0]==0xa5 && b[513]==0xa5);
    calls=0;assert(ae350_edid_read(b,128,&count)==AE350_EDID_CAPACITY);
    assert(count==1 && calls==1);
    calls=0;bad_header=1;
    assert(ae350_edid_read(b,sizeof(b),&count)==AE350_EDID_HEADER);
    assert(count==0 && calls==1);bad_header=0;
    calls=0;bad_checksum=1;
    assert(ae350_edid_read(b,sizeof(b),&count)==AE350_EDID_CHECKSUM);
    assert(count==1 && calls==2);bad_checksum=0;
    calls=0;fail_block=2;
    assert(ae350_edid_read(b,sizeof(b),&count)==BUS_NACK);
    assert(count==2 && calls==3);fail_block=256;
    calls=0;extensions=255;
    assert(!ae350_edid_read(b+1,32768,&count));
    assert(count==256 && calls==256 && b[32769]==0xa5);
    calls=0;extensions=0;
    assert(!ae350_edid_read(b,128,&count) && count==1 && calls==1);
    calls=0;
    assert(ae350_edid_read(0,128,&count)==BUS_INVALID && count==0);
    assert(ae350_edid_read(b,127,&count)==BUS_INVALID && count==0);
    assert(ae350_edid_read(b,128,0)==BUS_INVALID);
    assert(ae350_edid_read_block(256,b)==BUS_INVALID);
    assert(ae350_edid_read_block(0,0)==BUS_INVALID && calls==0);
    connected=0;
    assert(ae350_edid_read(b,128,&count)==AE350_EDID_DISCONNECTED && calls==0);
    connected=1;hpd_error=1;
    assert(ae350_edid_read(b,128,&count)==BUS_DEVICE && calls==0);
    hpd_error=0;unplug=1;
    assert(ae350_edid_read(b,128,&count)==AE350_EDID_DISCONNECTED && count==0 && calls==1);
    puts("PASS EDID block validation, capacity, extensions and errors");
}
