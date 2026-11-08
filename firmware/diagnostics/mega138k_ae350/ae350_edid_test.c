/* AE350 HDMI EDID reader: board_hdmi_edid_read_block(0/1) and dump
 * them plus the HPD pin register into the results area. */
#include <stdint.h>
#include "ae350_address_map.h"
#include "ae350_video.h"
#include "board_management.h"

static volatile uint32_t *out = (volatile uint32_t *)(uintptr_t)AE350_RESULTS_BASE;

__attribute__((noreturn)) void trap_report(unsigned cause, unsigned pc, unsigned value,
                         unsigned ra, unsigned sp) {
    out[8] = 0x80000000u | cause;
    out[9] = pc; out[10] = value; out[11] = ra; out[12] = sp;
    for (;;) __asm__ volatile("wfi");
}

static uint32_t read_reg(unsigned off) {
    return *(volatile uint32_t *)(uintptr_t)(AE350_VIDEO_BASE + off);
}

static uint8_t edid[256];

int main(void) {
    __asm__ volatile("csrw 0x7cc, 23\ncsrs 0x7ca, 3" ::: "memory");
    out[0] = 0xC3900010u;
    out[1] = (uint32_t)board_i2c_init();
    out[2] = read_reg(AE350_VIDEO_HPD);
    int rc0 = board_hdmi_edid_read_block(0, edid);
    out[3] = (uint32_t)rc0;
    /* word i = bytes [4i..4i+3] little-endian */
    for (unsigned i = 0; i < 32; i++)
        out[16 + i] = ((uint32_t)edid[4*i]) | ((uint32_t)edid[4*i+1] << 8) |
                      ((uint32_t)edid[4*i+2] << 16) | ((uint32_t)edid[4*i+3] << 24);
    int rc1 = board_hdmi_edid_read_block(1, edid + 128);
    out[4] = (uint32_t)rc1;
    out[5] = read_reg(AE350_VIDEO_HPD);
    /* probe raw DDC address too */
    out[6] = (uint32_t)board_i2c_probe(0x50);
    out[0] = 0xC3900030u;
    __asm__ volatile("fence" ::: "memory");
    for (;;) __asm__ volatile("wfi");
}
