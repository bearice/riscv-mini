/* AE350 video custom-mode board test: XRGB1555 format + programmable
 * timing/PLL via ae350_video_set_custom_mode, then back to mode 0.
 * Results are written to the standard AE350 results area. */
#include <stdint.h>
#include "ae350_address_map.h"
#include "ae350_video.h"
#include "ae350_dma.h"

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
    out[0] = 0xC3700010u;
    int rc = ae350_video_init_buffers(0, 0, 0);
    /* Programmatic 800x600@60 custom mode in XRGB1555. */
    rc = ae350_video_set_custom_mode(800, 600, 40, 128, 88, 1, 4, 23,
                                     60000, 1, 1, AE350_VIDEO_XRGB1555);
    out[1] = (uint32_t)rc;
    out[2] = read_reg(AE350_VIDEO_MODE);
    out[3] = read_reg(AE350_VIDEO_FORMAT);
    out[4] = read_reg(AE350_VIDEO_GEOMETRY);
    out[5] = read_reg(AE350_VIDEO_PIXEL_HZ);
    out[6] = read_reg(AE350_VIDEO_MODE_STATUS);
    struct ae350_video_mode cm;
    rc = ae350_video_get_mode(AE350_VIDEO_MODE_CUSTOM, &cm);
    out[7] = (uint32_t)rc;
    out[8] = cm.width | (cm.height << 16);
    out[9] = cm.pixel_hz;
    out[10] = cm.refresh_millihz;
    /* Invalid custom geometry must be rejected by the driver. */
    rc = ae350_video_set_custom_mode(0, 600, 0, 0, 0, 0, 0, 0, 60000, 0, 0, 0);
    out[11] = (uint32_t)rc;
    /* Back to the fixed 640x480 mode, RGB565. */
    rc = ae350_video_set_mode(0, AE350_VIDEO_RGB565);
    out[12] = (uint32_t)rc;
    out[13] = read_reg(AE350_VIDEO_MODE);
    out[14] = read_reg(AE350_VIDEO_GEOMETRY);
    out[0] = 0xC3700030u;
    __asm__ volatile("fence" ::: "memory");
    for (;;) __asm__ volatile("wfi");
}
