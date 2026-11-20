/* On-board GFX3D hardware verification for mega138k_ae350.
 *
 * Tests:
 *   1. Affine textured triangle via TMEM + TriRasterizer + DDR3 writeback
 *   2. Perspective-correct textured triangle (PerspRecipUnit 1/Q)
 *   3. Gouraud-shaded triangle with Early-Z / Late-Z enabled
 *
 * Prints PASS/FAIL and pixel/cycle counts to UART and writes AE350_RESULTS_BASE.
 */
#include <stdint.h>
#include "ae350_address_map.h"
#include "ae350_dma.h"
#include "ae350_gfx2d.h"

#define UART_DATA 0x20u
#define UART_LSR  0x34u
#define UART_REG(offset) (*(volatile uint32_t *)(AE350_UART_BASE + (offset)))

#define GFX3D_REG(off) (*(volatile uint32_t *)(AE350_GFX2D_BASE + (off)))

#define G3D_TRI(i)      GFX3D_REG(0x100u + 4u * (i))
#define G3D_CTRL        GFX3D_REG(0x160u)
#define G3D_STATUS      GFX3D_REG(0x164u)
#define G3D_FB_ADDR     GFX3D_REG(0x168u)
#define G3D_FB_STRIDE   GFX3D_REG(0x16Cu)
#define G3D_TEX_CFG     GFX3D_REG(0x170u)
#define G3D_BG_COLOR    GFX3D_REG(0x174u)
#define G3D_Z_CFG       GFX3D_REG(0x178u)
#define G3D_ZBUF_ADDR   GFX3D_REG(0x17Cu)
#define G3D_TMEM_ADDR   GFX3D_REG(0x180u)
#define G3D_TMEM_DATA   GFX3D_REG(0x184u)
#define G3D_PIXELS_OUT  GFX3D_REG(0x188u)

#define RES ((volatile uint32_t *)AE350_RESULTS_BASE)

static void putc_uart(char c) {
    while (!(UART_REG(UART_LSR) & 0x20u)) {}
    UART_REG(UART_DATA) = (uint8_t)c;
}
static void puts_uart(const char *s) { while (*s) putc_uart(*s++); }
static void hex32(uint32_t v) {
    for (int i = 28; i >= 0; i -= 4) putc_uart("0123456789abcdef"[(v >> i) & 15u]);
}

static uint32_t mcycle32(void) {
    uint32_t v;
    __asm__ volatile("csrr %0, mcycle" : "=r"(v));
    return v;
}

static int g3d_wait(uint32_t timeout) {
    while (timeout--) {
        uint32_t st = G3D_STATUS;
        if (!(st & 1u)) {
            return (st & 4u) ? -2 : 0;
        }
    }
    return -1;
}

static void submit_tri(uint32_t flags,
                       int16_t x0, int16_t y0, uint32_t z0, int32_t s0, int32_t t0, uint32_t q0, uint32_t c0,
                       int16_t x1, int16_t y1, uint32_t z1, int32_t s1, int32_t t1, uint32_t q1, uint32_t c1,
                       int16_t x2, int16_t y2, uint32_t z2, int32_t s2, int32_t t2, uint32_t q2, uint32_t c2,
                       uint16_t sc_min_x, uint16_t sc_min_y, uint16_t sc_max_x, uint16_t sc_max_y) {
    G3D_TRI(0) = flags;
    G3D_TRI(1) = 0;
    G3D_TRI(2) = 0;

    G3D_TRI(3) = ((uint32_t)(uint16_t)y0 << 16) | (uint16_t)x0;
    G3D_TRI(4) = z0;
    G3D_TRI(5) = (uint32_t)s0;
    G3D_TRI(6) = (uint32_t)t0;
    G3D_TRI(7) = q0;
    G3D_TRI(8) = c0;

    G3D_TRI(9)  = ((uint32_t)(uint16_t)y1 << 16) | (uint16_t)x1;
    G3D_TRI(10) = z1;
    G3D_TRI(11) = (uint32_t)s1;
    G3D_TRI(12) = (uint32_t)t1;
    G3D_TRI(13) = q1;
    G3D_TRI(14) = c1;

    G3D_TRI(15) = ((uint32_t)(uint16_t)y2 << 16) | (uint16_t)x2;
    G3D_TRI(16) = z2;
    G3D_TRI(17) = (uint32_t)s2;
    G3D_TRI(18) = (uint32_t)t2;
    G3D_TRI(19) = q2;
    G3D_TRI(20) = c2;

    G3D_TRI(21) = ((uint32_t)sc_min_y << 16) | sc_min_x;
    G3D_TRI(22) = ((uint32_t)sc_max_y << 16) | sc_max_x;
    G3D_TRI(23) = 0;

    G3D_CTRL = 1u;
}

int main(void) {
    for (unsigned i = 0; i < 16; i++) RES[i] = 0;
    RES[0] = 0xC3503D01u;

    puts_uart("\r\n=== AE350 GFX3D On-Board Test ===\r\n");

    if (ae350_gfx2d_probe()) {
        puts_uart("FAIL: gfx2d_probe failed\r\n");
        RES[0] = 0xC3503DFFu;
        return 1;
    }

    const uint32_t fb_addr = AE350_DIAG_VIDEO_FB0;
    const uint32_t stride = 64u;
    const uint32_t fb_bytes = stride * 64u * 4u;
    volatile uint32_t *fb = (volatile uint32_t *)(uintptr_t)fb_addr;
    for (uint32_t i = 0; i < stride * 64u; i++) fb[i] = 0u;
    ae350_dma_sync_for_device(fb_addr, fb_bytes);

    /* Upload 8x8 checkerboard texture to TMEM (log2_w=3, log2_h=3) */
    G3D_FB_ADDR = fb_addr;
    G3D_FB_STRIDE = stride;
    G3D_TEX_CFG = (1u << 0) | (3u << 5) | (3u << 8);
    G3D_BG_COLOR = 0xFF000000u;
    G3D_Z_CFG = (0u << 0) | (0u << 1) | (7u << 2) | (1u << 5);
    G3D_TMEM_ADDR = 0u;
    for (uint32_t v = 0; v < 8; v++) {
        for (uint32_t u = 0; u < 8; u++) {
            uint32_t c = ((u ^ v) & 1u) ? (0xFF000000u | (u * 30u) | ((v * 30u) << 8) | 0x00C00000u)
                                        : (0xFF204080u);
            G3D_TMEM_DATA = c;
        }
    }

    int fails = 0;

    /* Test 1: Affine textured triangle (4,4)-(28,4)-(4,28) */
    uint32_t t0 = mcycle32();
    submit_tri(0u,
               4 << 4, 4 << 4, 0x10000000u, 0, 0, 1u << 30, 0xFFFFFFFFu,
               28 << 4, 4 << 4, 0x10000000u, 7 << 20, 0, 1u << 30, 0xFFFFFFFFu,
               4 << 4, 28 << 4, 0x10000000u, 0, 7 << 20, 1u << 30, 0xFFFFFFFFu,
               0, 0, 63, 63);
    int rc1 = g3d_wait(0x100000u);
    uint32_t cyc1 = mcycle32() - t0;
    uint32_t pix1 = G3D_PIXELS_OUT;
    ae350_dma_sync_for_cpu(fb_addr, fb_bytes);
    uint32_t p_inside = fb[8 * stride + 8];
    uint32_t p_outside = fb[0 * stride + 0];
    puts_uart("T1 affine_tex rc="); hex32((uint32_t)rc1);
    puts_uart(" pix="); hex32(pix1);
    puts_uart(" cyc="); hex32(cyc1);
    puts_uart(" in="); hex32(p_inside);
    puts_uart(" out="); hex32(p_outside);
    puts_uart("\r\n");
    if (rc1 != 0 || pix1 == 0 || p_inside == 0 || p_outside != 0) fails++;

    /* Test 2: Perspective-correct textured triangle (32,4)-(56,4)-(32,28) */
    G3D_TEX_CFG = (1u << 0) | (1u << 1) | (3u << 5) | (3u << 8); /* bilinear_en=1 */
    uint32_t t1 = mcycle32();
    submit_tri(1u, /* persp_en=1 */
               32 << 4, 4 << 4, 0x10000000u, 0, 0, 1u << 30, 0xFFFFFFFFu,
               56 << 4, 4 << 4, 0x10000000u, (7 << 19), 0, 1u << 29, 0xFFFFFFFFu,
               32 << 4, 28 << 4, 0x10000000u, 0, (7 << 18), 1u << 28, 0xFFFFFFFFu,
               0, 0, 63, 63);
    int rc2 = g3d_wait(0x100000u);
    uint32_t cyc2 = mcycle32() - t1;
    uint32_t pix2 = G3D_PIXELS_OUT;
    ae350_dma_sync_for_cpu(fb_addr, fb_bytes);
    uint32_t p_persp = fb[8 * stride + 36];
    puts_uart("T2 persp_bil rc="); hex32((uint32_t)rc2);
    puts_uart(" pix="); hex32(pix2);
    puts_uart(" cyc="); hex32(cyc2);
    puts_uart(" in="); hex32(p_persp);
    puts_uart("\r\n");
    if (rc2 != 0 || pix2 == 0 || p_persp == 0) fails++;

    /* Test 3: Gouraud-shaded untextured triangle (8,32)-(48,32)-(28,56) */
    G3D_TEX_CFG = 0u;
    G3D_Z_CFG = (1u << 0) | (1u << 1) | (7u << 2) | (1u << 5); /* Z_ALWAYS + Z_WRITE */
    uint32_t t2 = mcycle32();
    submit_tri(2u, /* gouraud_en=1 */
               8 << 4, 32 << 4, 0x40000000u, 0, 0, 1u << 30, 0xFFFF0000u,
               48 << 4, 32 << 4, 0x40000000u, 0, 0, 1u << 30, 0xFF00FF00u,
               28 << 4, 56 << 4, 0x40000000u, 0, 0, 1u << 30, 0xFF0000FFu,
               0, 0, 63, 63);
    int rc3 = g3d_wait(0x100000u);
    uint32_t cyc3 = mcycle32() - t2;
    uint32_t pix3 = G3D_PIXELS_OUT;
    ae350_dma_sync_for_cpu(fb_addr, fb_bytes);
    uint32_t p_gour = fb[40 * stride + 28];
    puts_uart("T3 gouraud_z rc="); hex32((uint32_t)rc3);
    puts_uart(" pix="); hex32(pix3);
    puts_uart(" cyc="); hex32(cyc3);
    puts_uart(" in="); hex32(p_gour);
    puts_uart("\r\n");
    if (rc3 != 0 || pix3 == 0 || p_gour == 0) fails++;

    RES[1] = pix1;
    RES[2] = pix2;
    RES[3] = pix3;
    RES[4] = (uint32_t)fails;
    RES[0] = (fails == 0) ? 0xC3503D00u : 0xC3503DFFu;

    if (fails == 0) puts_uart("GFX3D_BOARD_TEST PASS\r\n");
    else puts_uart("GFX3D_BOARD_TEST FAIL\r\n");

    __asm__ volatile("fence" ::: "memory");
    for (;;) __asm__ volatile("wfi");
}
