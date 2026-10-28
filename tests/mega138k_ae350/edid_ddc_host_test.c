/* Exercise the actual BSP mux and E-DDC transaction orchestration. The already
 * shared ATCIIC100 byte pump is replaced by a phase-level sink model. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#define AE350_MANAGEMENT_HOST_TEST
#include "board_management.c"
static uint32_t mux,iic_status,other,clock_tick;
static unsigned phase,segment,expected_offset,fail_phase=99;
volatile uint32_t *board_management_test_reg(uint32_t address) {
    if(address==AE350_GPIO_BASE+0x24 || address==AE350_GPIO_BASE+0x30)return &mux;
    if(address==AE350_I2C_BASE+0x18)return &iic_status;
    return &other;
}
uint32_t board_management_test_cycle(void) {return clock_tick+=AE350_CPU_HZ/500u;}
int board_management_test_phase(unsigned addr,const uint8_t *tx,uint8_t *rx,unsigned bytes,unsigned stop) {
    assert(mux==(3u<<AE350_TWI_MUX_GPIO_SHIFT));
    unsigned current=phase++;
    if(current==fail_phase)return BUS_NACK;
    if(segment && current==0) {
        assert(addr==0x30 && tx && tx[0]==segment && bytes==1 && !rx && !stop);
    } else if(current==(segment?1u:0u)) {
        assert(addr==0x50 && tx && tx[0]==expected_offset && bytes==1 && !rx && !stop);
    } else {
        assert(addr==0x50 && !tx && rx && bytes==128 && stop);
        memset(rx,0x5a,128);
    }
    return 0;
}
int main(void) {
    uint8_t output[130];
    for(unsigned block=0;block<256;block++) {
        i2c_ready=1;iic_status=IIC_LINES;mux=6u<<AE350_TWI_MUX_GPIO_SHIFT;
        phase=0;segment=block/2;expected_offset=(block&1u)*128u;
        memset(output,0xa5,sizeof(output));
        assert(!board_hdmi_edid_read_block(block,output+1));
        assert(phase==(segment?3u:2u) && i2c_ready);
        assert(mux==(6u<<AE350_TWI_MUX_GPIO_SHIFT));
        assert(output[0]==0xa5 && output[1]==0x5a && output[128]==0x5a && output[129]==0xa5);
    }
    for(fail_phase=0;fail_phase<3;fail_phase++) {
        i2c_ready=1;iic_status=IIC_LINES;mux=1u<<AE350_TWI_MUX_GPIO_SHIFT;
        phase=0;segment=1;expected_offset=0;
        assert(board_hdmi_edid_read_block(2,output)==BUS_NACK);
        assert(phase==fail_phase+1 && !i2c_ready);
        assert(mux==(1u<<AE350_TWI_MUX_GPIO_SHIFT));
    }
    phase=0;i2c_ready=0;
    assert(board_hdmi_edid_read_block(0,output)==BUS_DEVICE);
    assert(board_hdmi_edid_read_block(256,output)==BUS_INVALID);
    assert(board_hdmi_edid_read_block(0,0)==BUS_INVALID && !phase);
    i2c_ready=1;iic_status=IIC_LINES|IIC_BUSY;
    assert(board_hdmi_edid_read_block(0,output)==BUS_PROTOCOL && !phase);
    puts("PASS E-DDC phases, all segments, mux restoration and bus failures");
}
