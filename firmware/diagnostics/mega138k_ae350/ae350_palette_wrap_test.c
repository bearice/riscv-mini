/* Physical hardware verification for 256-color palette (INDEX8)
 * and scanout wrapback on Tang Mega 138K Pro AE350.
 */
#include <stdint.h>
#include "ae350_address_map.h"
#include "ae350_video.h"
#include "ae350_dma.h"

#define W(a) (*(volatile uint32_t *)(uintptr_t)(a))
static volatile uint32_t *const out = (volatile uint32_t *)AE350_RESULTS_BASE;

static void puts_uart(const char *s) {
    while (*s) {
        while (!(W(AE350_UART_BASE + 0x34u) & 0x20u)) {}
        W(AE350_UART_BASE + 0x20u) = (uint8_t)*s++;
    }
}

static void finish(unsigned code) {
    struct ae350_video_status s;
    out[1] = code;
    if (!ae350_video_status(&s)) {
        out[8] = s.frames;
        out[9] = s.underflows;
        out[10] = s.missing;
        out[11] = s.address_error;
        out[12] = s.timeout_error;
        out[13] = s.errors;
        out[14] = s.fifo_min;
        out[15] = s.max_wait;
        out[16] = s.reads;
        out[17] = s.cpu_commands;
        out[18] = s.request_overflow;
        out[19] = s.active_base;
        out[20] = s.width;
        out[21] = s.height;
        out[22] = s.stride;
        out[7] = s.enabled;
    }
    out[0] = (code == 0) ? 0xC6100001u : (0x80000000u | code);
    __asm__ volatile("fence" ::: "memory");
    if (code == 0) {
        puts_uart("PASS AE350 PALETTE AND WRAPBACK\r\n");
    } else {
        puts_uart("FAIL AE350 PALETTE AND WRAPBACK\r\n");
    }
    for (;;) __asm__ volatile("wfi");
}

int main(void) {
    for (unsigned i = 0; i < 32; i++) out[i] = 0;
    out[31] = 0x50414C57u; /* 'PALW' */
    out[0] = 1;
    __asm__ volatile("csrw 0x7cc, %0\n\tcsrs 0x7ca, %1" :: "r"(23), "r"(2) : "memory");

    puts_uart("AE350 PALETTE WRAP TEST START\r\n");

    /* 1. Check capability flags */
    uint32_t cap = W(AE350_VIDEO_BASE + 8);
    if (!(cap & (1u << 11))) finish(0x11); /* Palette not supported */
    if (!(cap & (1u << 12))) finish(0x12); /* Wrap not supported */

    /* 2. Switch to 640x480 INDEX8 */
    int rc = ae350_video_set_mode(0, AE350_VIDEO_INDEX8);
    if (rc != 0) finish(0x20 | (unsigned)rc);

    /* 3. Program Palette Bank 0: 256 colors RGB565 */
    uint16_t pal0[256];
    for (unsigned i = 0; i < 256; i++) {
        /* Gradient: R=i>>3 (5b), G=(i*2)>>2 (6b), B=(255-i)>>3 (5b) */
        unsigned r = (i >> 3) & 0x1F;
        unsigned g = ((i * 63) / 255) & 0x3F;
        unsigned b = ((255 - i) >> 3) & 0x1F;
        pal0[i] = (uint16_t)((r << 11) | (g << 5) | b);
    }
    /* Write with readback enabled */
    rc = ae350_video_palette(pal0, 0, 256, 1);
    if (rc != 0) finish(0x30 | (unsigned)rc);

    /* 4. Program Palette Bank 1: inverted palette */
    uint16_t pal1[256];
    for (unsigned i = 0; i < 256; i++) {
        pal1[i] = (uint16_t)~pal0[i];
    }
    rc = ae350_video_palette(pal1, 1, 256, 1);
    if (rc != 0) finish(0x40 | (unsigned)rc);

    /* 5. Initialize Framebuffers in INDEX8: 640 bytes per row */
    uint8_t *fb0 = (uint8_t *)(uintptr_t)AE350_DIAG_VIDEO_FB0;
    uint8_t *fb1 = (uint8_t *)(uintptr_t)AE350_DIAG_VIDEO_FB1;
    rc = ae350_video_init_buffers(fb0, fb1, 640);
    if (rc != 0) finish(0x50 | (unsigned)rc);

    /* 6. Draw color bars pattern into FB0 and FB1 */
    uint8_t col_lut[640];
    for (unsigned x = 0; x < 640; x++) {
        col_lut[x] = (uint8_t)((x * 256) / 640);
    }
    for (unsigned y = 0; y < 480; y++) {
        uint8_t *r0 = fb0 + y * 640;
        uint8_t *r1 = fb1 + y * 640;
        for (unsigned x = 0; x < 640; x++) {
            r0[x] = col_lut[x];
            r1[x] = col_lut[x] ^ 0x80;
        }
    }
    ae350_dma_sync_for_device((uintptr_t)fb0, 640 * 480);
    ae350_dma_sync_for_device((uintptr_t)fb1, 640 * 480);

    /* 7. Test Presentation with Palette Bank 0 */
    rc = ae350_video_present_indexed(0, 0);
    if (rc != 0) finish(0x60 | (unsigned)rc);

    /* Wait a few frames */
    uint32_t start_frames = W(AE350_VIDEO_BASE + VIDEO_FRAMES);
    while (W(AE350_VIDEO_BASE + VIDEO_FRAMES) - start_frames < 30) {}

    /* Active bank should be 0 */
    if (ae350_video_palette_active() != 0) finish(0x70);

    /* 8. Switch to Palette Bank 1 */
    rc = ae350_video_present_indexed(0, 1);
    if (rc != 0) finish(0x80 | (unsigned)rc);

    start_frames = W(AE350_VIDEO_BASE + VIDEO_FRAMES);
    while (W(AE350_VIDEO_BASE + VIDEO_FRAMES) - start_frames < 30) {}

    if (ae350_video_palette_active() != 1) finish(0x85);

    /* 9. Test Wrap-back Scanout */
    /* Set wrap boundary: wrap window from FB0 to FB0 + 640*240 (half height wrap) */
    uint32_t wrap_start = (uint32_t)(uintptr_t)fb0;
    uint32_t wrap_end = wrap_start + (640 * 240);
    rc = ae350_video_set_wrap(wrap_start, wrap_end);
    if (rc != 0) finish(0x90 | (unsigned)rc);

    /* Present frame 0 with wrap */
    rc = ae350_video_present_indexed(0, 0);
    if (rc != 0) finish(0x95 | (unsigned)rc);

    start_frames = W(AE350_VIDEO_BASE + VIDEO_FRAMES);
    while (W(AE350_VIDEO_BASE + VIDEO_FRAMES) - start_frames < 60) {}

    /* Disable wrap: set wrap_start = 0, wrap_end = 0 */
    rc = ae350_video_set_wrap(0, 0);
    if (rc != 0) finish(0xA0 | (unsigned)rc);
    rc = ae350_video_present_indexed(0, 0);
    if (rc != 0) finish(0xA5 | (unsigned)rc);

    start_frames = W(AE350_VIDEO_BASE + VIDEO_FRAMES);
    while (W(AE350_VIDEO_BASE + VIDEO_FRAMES) - start_frames < 60) {}

    /* 10. Check health counters */
    struct ae350_video_status st;
    if (ae350_video_status(&st)) finish(0xB0);
    if (st.underflows != 0) finish(0xB1);
    if (st.errors != 0) finish(0xB2);
    if (st.address_error != 0) finish(0xB3);
    if (st.timeout_error != 0) finish(0xB4);

    finish(0); /* All tests passed */
    return 0;
}
