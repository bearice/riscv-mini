/* Board bring-up: generated JEDEC init, bounded DFII read training, memtest.
 * The probe follows LiteX liblitedram's ACT/write/read/PRE training flow.
 * Every tap is checked with three distinct patterns; select widest window.
 */
#include <stdint.h>
#include <generated/sdram_phy.h>
#include "ddr_app.h"

extern void puts_uart(const char *);
extern void putchar_uart(char);
extern unsigned ddr_call(unsigned entry, unsigned stack);

void cdelay(int count) {
    for (volatile int i=0; i<count; ++i) __asm__ volatile("nop");
}
static void hex(unsigned x) {
    const char *digits="0123456789abcdef";
    for (int i=28; i>=0; i-=4) putchar_uart(digits[(x>>i)&15]);
}
static void command(unsigned phase, unsigned value) {
    if (phase) command_p1(value); else command_p0(value);
}
static void zero_address(void) {
    sdram_dfii_pi0_address_write(0); sdram_dfii_pi0_baddress_write(0);
    sdram_dfii_pi1_address_write(0); sdram_dfii_pi1_baddress_write(0);
}
static void delay_reset(void) {
    ddrphy_rdly_dq_rst_write(1);
    ddrphy_rdly_dq_dir_write(1);
    for (unsigned i=0; i<SDRAM_PHY_DELAYS-1; ++i) ddrphy_rdly_dq_inc_write(1);
    ddrphy_rdly_dq_dir_write(0);
}
static int probe(unsigned lane, unsigned seed) {
    /* Selection asserts DQS HOLD. Release it for actual read/write traffic. */
    ddrphy_dly_sel_write(0);
    uint64_t a=((uint64_t)(0x19e34ab7u ^ seed)<<32) | (0xbc08f56du ^ seed*0x10204081u);
    uint64_t b=~a ^ UINT64_C(0x792e5104c3a86df0);
    zero_address();
    command_p0(DFII_COMMAND_RAS|DFII_COMMAND_CS); cdelay(15);
    sdram_dfii_pi0_wrdata_write(a); sdram_dfii_pi1_wrdata_write(b);
    command(SDRAM_PHY_WRPHASE, DFII_COMMAND_CAS|DFII_COMMAND_WE|DFII_COMMAND_CS|DFII_COMMAND_WRDATA);
    cdelay(15); ddrphy_burstdet_clr_write(1);
    command(SDRAM_PHY_RDPHASE, DFII_COMMAND_CAS|DFII_COMMAND_CS|DFII_COMMAND_RDDATA);
    cdelay(15);
    uint64_t ar=sdram_dfii_pi0_rddata_read(), br=sdram_dfii_pi1_rddata_read();
    unsigned seen=ddrphy_burstdet_seen_read();
    command_p0(DFII_COMMAND_RAS|DFII_COMMAND_WE|DFII_COMMAND_CS); cdelay(15);
    command_p0(DFII_COMMAND_RAS|DFII_COMMAND_CAS|DFII_COMMAND_CS); cdelay(32);
    uint64_t mask=UINT64_C(0x00ff00ff00ff00ff) << (lane*8);
    ddrphy_dly_sel_write(1u<<lane);
    return ((seen>>lane)&1) && !(((a^ar)|(b^br))&mask);
}
static int train(void) {
    for (unsigned lane=0; lane<SDRAM_PHY_MODULES; ++lane) {
        unsigned best_len=0, best_tap=0, best_slip=0;
        ddrphy_dly_sel_write(1u<<lane);
        ddrphy_rdly_dq_bitslip_rst_write(1);
        for (unsigned slip=0; slip<SDRAM_PHY_BITSLIPS; ++slip) {
            delay_reset();
            unsigned start=0, length=0;
            for (unsigned tap=0; tap<SDRAM_PHY_DELAYS; ++tap) {
                int ok=probe(lane,42) && probe(lane,84) && probe(lane,36);
                if (ok) {
                    if (!length) start=tap;
                    ++length;
                    if (length>best_len) {
                        best_len=length; best_tap=start+length/2; best_slip=slip;
                    }
                } else length=0;
                ddrphy_rdly_dq_inc_write(1);
            }
            ddrphy_rdly_dq_bitslip_write(1);
        }
        puts_uart("lane="); hex(lane); puts_uart(" window="); hex(best_len);
        puts_uart(" slip="); hex(best_slip); puts_uart(" tap="); hex(best_tap); puts_uart("\r\n");
        if (best_len<4) { ddrphy_dly_sel_write(0); return 0; }
        ddrphy_rdly_dq_bitslip_rst_write(1);
        for (unsigned i=0; i<best_slip; ++i) ddrphy_rdly_dq_bitslip_write(1);
        delay_reset();
        for (unsigned i=0; i<best_tap; ++i) ddrphy_rdly_dq_inc_write(1);
        if (!(probe(lane,42) && probe(lane,84) && probe(lane,36))) {
            ddrphy_dly_sel_write(0); return 0;
        }
    }
    ddrphy_dly_sel_write(0);
    return 1;
}
static int mismatch(unsigned addr, unsigned wanted, unsigned got) {
    puts_uart("DDR mismatch addr="); hex(addr); puts_uart(" expected="); hex(wanted);
    puts_uart(" got="); hex(got); puts_uart("\r\n"); return 0;
}
static int memory_test(void) {
    volatile unsigned *base=(volatile unsigned *)0x40000000u;
    for (unsigned bit=0; bit<32; ++bit) {
        base[0]=1u<<bit; if (base[0]!=(1u<<bit)) return mismatch(0x40000000,1u<<bit,base[0]);
        base[0]=~(1u<<bit); if (base[0]!=~(1u<<bit)) return mismatch(0x40000000,~(1u<<bit),base[0]);
    }
    /* Simultaneous signatures expose aliasing on every word address bit. */
    base[0]=0xabcdef01;
    for (unsigned off=1; off<(128u*1024*1024/4); off<<=1) base[off]=0x12345678^off;
    if (base[0]!=0xabcdef01) return mismatch(0x40000000,0xabcdef01,base[0]);
    for (unsigned off=1; off<(128u*1024*1024/4); off<<=1)
        if (base[off]!=(0x12345678^off)) return mismatch(0x40000000+4*off,0x12345678^off,base[off]);
    /* 64 KiB at each MiB: all banks/rows across the entire 128 MiB aperture. */
    for (unsigned mode=0; mode<4; ++mode) {
        for (unsigned region=0; region<128; ++region) {
            volatile unsigned *p=base+region*(1024*1024/4);
            for (unsigned i=0; i<16384; ++i) p[i]=mode==0 ? 0 : mode==1 ? ~0u : ((unsigned)(p+i)*0x9e3779b9u)^(mode==2 ? 0 : ~0u);
        }
        cdelay(48000);
        for (unsigned region=0; region<128; ++region) {
            volatile unsigned *p=base+region*(1024*1024/4);
            for (unsigned i=0; i<16384; ++i) {
                unsigned v=mode==0 ? 0 : mode==1 ? ~0u : ((unsigned)(p+i)*0x9e3779b9u)^(mode==2 ? 0 : ~0u);
                if (p[i]!=v) return mismatch((unsigned)(p+i),v,p[i]);
            }
        }
        puts_uart("DDR pattern pass "); hex(mode); puts_uart("\r\n");
    }
    volatile unsigned *last=(volatile unsigned *)0x47fffffcu;
    *last=0xdeadbeef;
    if (*last!=0xdeadbeef) return mismatch((unsigned)last,0xdeadbeef,*last);
    return 1;
}
int ddr_bringup(void) {
    puts_uart("DDR JEDEC init: CK96 MHz DLL-off CL6/CWL6 ODT disabled\r\n");
    sdram_dfii_control_write(0);
    cdelay(50000);
    init_sequence();
    puts_uart("DDR read training...\r\n");
    if (!train()) { puts_uart("DDR TRAIN FAILED\r\n"); return 0; }
    sdram_dfii_control_write(DFII_CONTROL_SEL);
    puts_uart("DDR controller memtest...\r\n");
    if (!memory_test()) return 0;
    volatile unsigned char *dest=(volatile unsigned char *)0x40100000u;
    for (unsigned i=0; i<sizeof(ddr_app); ++i) dest[i]=ddr_app[i];
    __asm__ volatile("fence\n.word 0x0000100f" ::: "memory");
    unsigned result=ddr_call(0x40100000u,0x40200000u);
    puts_uart("DDR C execution result="); hex(result); puts_uart("\r\n");
    if (result!=0x13579bdfu) return 0;
    puts_uart("M1 PASS: DDR sampled patterns + address/data bits + DDR code/stack\r\n");
    return 1;
}
