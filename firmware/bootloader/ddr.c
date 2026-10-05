/* DDR3 DLL-off, CL6/CWL6. Runs entirely from ROM with a pinned L2 stack.
 * Training scratch is bank0/row0/column0, separate from the boot stack.
 */
#include <generated/csr.h>
#include "io.h"

enum { RAS=1u<<20, CAS=1u<<21, WE=1u<<22, WR=1u<<23, RD=1u<<24 };
static void settle(void) { for(unsigned i=0;i<80;++i)__asm__ volatile("nop"); }
static void command(unsigned bits,unsigned bank,unsigned address) {
    sdram_boot_command_write(bits | bank<<16 | address);settle();
}
static void tune(unsigned lane,unsigned bits) { sdram_boot_tuning_write((1u<<lane)|bits);settle(); }
static void delay_reset(unsigned lane) {
    tune(lane,(1u<<2)|(1u<<4));
    for(unsigned i=0;i<255;++i)tune(lane,(1u<<3)|(1u<<4));
}
static void delay_step(unsigned lane) { tune(lane,1u<<3); }
static int probe(unsigned lane) {
    unsigned good=1;
    sdram_boot_tuning_write(0);settle();
    for(unsigned pattern=0;pattern<3;++pattern) {
        sdram_boot_pattern_write(pattern);
        command(CAS|WE,0,0);                 /* ACT */
        command(RAS|WR,0,0);                 /* WRITE */
        command(RAS|WE|RD,0,0);              /* READ + fresh capture */
        unsigned status=sdram_boot_status_read();
        good &= !!(status&4u) & !!(status&(16u<<lane)) & !!(status&(64u<<lane));
        command(CAS,0,0);                    /* PRECHARGE */
        command(WE,0,0);                     /* REFRESH */
    }
    tune(lane,0);
    return good;
}
static int train(unsigned lane) {
    unsigned best_len=0,best_tap=0,best_slip=0;
    tune(lane,1u<<5);
    for(unsigned slip=0;slip<4;++slip) {
        unsigned run=0,start=0;
        delay_reset(lane);
        for(unsigned tap=0;tap<256;++tap) {
            if(probe(lane)) {
                if(!run)start=tap;
                ++run;
                if(run>best_len) {best_len=run;best_tap=start+(run>>1);best_slip=slip;}
            } else run=0;
            if(tap!=255)delay_step(lane);
        }
        if(slip!=3)tune(lane,1u<<6);
    }
    puts_uart("DDR LANE ");putchar_uart('0'+lane);puts_uart(" tap/slip/window=");
    io_hex(best_tap|(best_slip<<8)|(best_len<<16));puts_uart(" status=");
    io_hex(sdram_boot_status_read());puts_uart("\r\n");
    if(best_len<4)return 0;
    tune(lane,1u<<5);
    for(unsigned i=0;i<best_slip;++i)tune(lane,1u<<6);
    delay_reset(lane);
    for(unsigned i=0;i<best_tap;++i)delay_step(lane);
    if(!probe(lane))return 0;
    unsigned result=best_tap|(best_slip<<8)|(best_len<<16);
    if(lane)sdram_boot_lane1_write(result);else sdram_boot_lane0_write(result);
    return 1;
}
int ddr_init(void) {
    for(unsigned attempt=0;attempt<3;++attempt) {
        puts_uart("DDR INIT ");putchar_uart('1'+attempt);puts_uart("\r\n");
        sdram_boot_tuning_write(0);sdram_boot_control_write(0);io_delay_ms(1);
        sdram_boot_control_write(1);io_delay_ms(1);
        sdram_boot_control_write(3);settle();
        command(0,2,0x8);command(0,3,0);command(0,1,0x3);command(0,0,0x320);
        command(RAS|CAS,0,0x400);             /* ZQCL */
        if(train(0) && train(1)) {
            sdram_boot_tuning_write(0);
            __asm__ volatile("fence rw,rw" ::: "memory");
            sdram_boot_control_write(7);settle();
            puts_uart("DDR READY\r\n");return 1;
        }
    }
    sdram_boot_tuning_write(0);sdram_boot_control_write(11);
    puts_uart("ERR DDR INIT; reset\r\n");return 0;
}
