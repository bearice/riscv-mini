#include <stdint.h>
#include "ae350_address_map.h"
#include "ae350_tty.h"
#include "ae350_hdmi_console_demo.h"
static struct ae350_tty tty;
int main(void) {
    __asm__ volatile("csrw 0x7cc,%0\n\tcsrs 0x7ca,%1"::"r"(23),"r"(2):"memory");
    volatile uint32_t *out=(volatile uint32_t *)AE350_RESULTS_BASE;
    out[0]=0xc6101000u;out[1]=ae350_video_init((uint16_t *)0x01000000u,(uint16_t *)0x01100000u,1280);
    if(!out[1]) {
        ae350_tty_init(&tty);ae350_tty_write(&tty,ae350_hdmi_console_text);
        out[1]=ae350_tty_flush(&tty);
    }
    out[0]=0xc6101001u;__asm__ volatile("fence":::"memory");
    for(;;)__asm__ volatile("wfi");
}
