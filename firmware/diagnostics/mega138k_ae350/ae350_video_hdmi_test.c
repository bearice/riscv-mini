/* AE350 HDMI output test: switch to custom 800x600@60 XRGB1555,
 * present a GFX2D test pattern, report HPD/geometry registers, then
 * keep transmitting forever so a capture card can grab it. */
#include <stdint.h>
#include "ae350_address_map.h"
#include "ae350_video.h"
#include "ae350_gfx2d.h"

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

int main(void) {
    __asm__ volatile("csrw 0x7cc, 23\ncsrs 0x7ca, 3" ::: "memory");
    out[0] = 0xC3800010u;
    /* Custom 800x600@60 in XRGB1555 (set_custom_mode leaves output off). */
    int rc = ae350_video_set_custom_mode(800, 600, 40, 128, 88, 1, 4, 23,
                                         60000, 1, 1, AE350_VIDEO_XRGB1555);
    out[1] = (uint32_t)rc;
    out[2] = read_reg(AE350_VIDEO_MODE);
    out[3] = read_reg(AE350_VIDEO_FORMAT);
    out[4] = read_reg(AE350_VIDEO_GEOMETRY);
    out[5] = read_reg(AE350_VIDEO_PIXEL_HZ);
    rc = ae350_video_init_buffers((void *)(uintptr_t)AE350_DIAG_VIDEO_FB0,
                                (void *)(uintptr_t)AE350_DIAG_VIDEO_FB1, 0);
    out[6] = (uint32_t)rc;
    struct ae350_video_status st;
    ae350_video_status(&st);
    uint16_t *fb = (uint16_t *)(st.active ? AE350_DIAG_VIDEO_FB1 : AE350_DIAG_VIDEO_FB0);
    /* Test pattern: left half red (0xF800), right half green (0x07E0),
     * top band blue (0x001F). XRGB1555 R[14:10] G[9:5] B[4:0]. */
    rc = ae350_gfx2d_fill_rect((uint32_t)(uintptr_t)fb, 1600, 800, 600, 0x001F);
    ae350_gfx2d_wait(0xffff);
    rc = ae350_gfx2d_fill_rect((uint32_t)(uintptr_t)fb, 1600, 400, 600, 0x7C00);
    ae350_gfx2d_wait(0xffff);
    rc = ae350_gfx2d_fill_rect((uint32_t)(uintptr_t)(fb + 400), 1600, 400, 600, 0x03E0);
    ae350_gfx2d_wait(0xffff);
    out[7] = (uint32_t)rc;
    out[8] = read_reg(AE350_VIDEO_HPD);
    rc = ae350_video_present(0);
    out[9] = (uint32_t)rc;
    out[10] = read_reg(AE350_VIDEO_ENABLE);
    out[11] = read_reg(AE350_VIDEO_STATUS);
    out[0] = 0xC3800030u;
    __asm__ volatile("fence" ::: "memory");
    for (;;) __asm__ volatile("wfi");
}
