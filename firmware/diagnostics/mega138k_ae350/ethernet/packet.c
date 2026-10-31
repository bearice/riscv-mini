/* Isolated-link DDR DMA ring ARP/ICMP/UDP echo & RX timing/error diagnostic. */
#include <stdint.h>
#include <string.h>
#include "ae350_address_map.h"
#include "ethernet_phy.h"
#include "ethernet_dma.h"
#include "ethernet_echo.h"
#ifndef ETH_TEST_MS
#define ETH_TEST_MS 60000u
#endif
#define W(a) (*(volatile uint32_t *)(uintptr_t)(a))
static volatile uint32_t *const r=(volatile uint32_t *)AE350_RESULTS_BASE;
static const struct ethernet_endpoint endpoint={.mac={2,0x35,0,0,0,1},.ipv4={192,0,2,2},.udp_port=32768};
static void out(const char *s) {while(*s) {while(!(W(0xf0300034u)&0x20)){}W(0xf0300020u)=(uint8_t)*s++;}}
static void hex(uint32_t v) {for(int n=28;n>=0;n-=4) {char b[2]={"0123456789abcdef"[(v>>n)&15],0};out(b);}}
static void hex1(uint32_t v) {char b[2]={"0123456789abcdef"[v&15],0};out(b);}
static const char *parse_hex(const char *p,uint32_t *out_val) {
    while(*p==' '||*p=='\t')p++;
    uint32_t v=0;unsigned digits=0;
    while(*p) {
        char c=*p;uint32_t d=0;
        if(c>='0'&&c<='9')d=(uint32_t)(c-'0');
        else if(c>='a'&&c<='f')d=(uint32_t)(c-'a'+10);
        else if(c>='A'&&c<='F')d=(uint32_t)(c-'A'+10);
        else break;
        v=(v<<4)|d;digits++;p++;
    }
    if(!digits)return 0;
    *out_val=v;return p;
}
static struct {
    unsigned crc,preamble,mac_drop,drop,error,phy_rx,phy_tx,phy_rx_er,rx_er,fcs;
} base;
static void reset_counters(unsigned phy) {
    for(unsigned i=6;i<=14;i++)r[i]=0;
    for(unsigned i=114;i<=122;i++)r[i]=0;
    for(unsigned i=135;i<=158;i++)r[i]=0;
    r[128]=0;W(AE350_ETH_BASE+0xa4)=7;
    W(AE350_ETH_BASE+0x70)=3;
    uint16_t dummy=0;ethernet_mdio_read(phy,10,&dummy);
    base.crc=ethernet_packet_reg(0x4c);
    base.preamble=ethernet_packet_reg(0x50);
    base.mac_drop=ethernet_packet_reg(0x5c);
    base.drop=ethernet_packet_reg(0x40);
    base.error=ethernet_packet_reg(0x44);
    base.phy_rx=ethernet_packet_reg(0x54);
    base.phy_tx=ethernet_packet_reg(0x58);
    base.phy_rx_er=ethernet_packet_reg(0x60);
    base.rx_er=ethernet_packet_reg(0x64);
    base.fcs=ethernet_packet_reg(0x48);
}
static void sample_counters(void) {
    r[121]=ethernet_packet_reg(0x5c)-base.mac_drop;
    r[122]=ethernet_packet_reg(0x40)-base.drop;
    r[12]=r[121]+r[122];
    r[116]=ethernet_packet_reg(0x4c)-base.crc;
    r[117]=ethernet_packet_reg(0x50)-base.preamble;
    unsigned dma_err=ethernet_packet_reg(0x44)-base.error;
    r[13]=dma_err+r[116]+r[117];
    r[118]=ethernet_packet_reg(0x38);
    r[119]=ethernet_packet_reg(0x58)-base.phy_tx;
    r[120]=ethernet_packet_reg(0x54)-base.phy_rx;
    r[135]=ethernet_packet_reg(0x60)-base.phy_rx_er;
    r[136]=ethernet_packet_reg(0x64)-base.rx_er;
    r[137]=ethernet_packet_reg(0x48)-base.fcs;
    r[138]=ethernet_packet_reg(0x68);
    r[139]=ethernet_packet_reg(0x6c);
    r[140]=ethernet_packet_reg(0x70);
    W(AE350_ETH_BASE+0x78)=0;
    r[141]=ethernet_packet_reg(0x74);
    if(r[140]&2u) {
        uint32_t er_pack=0;
        for(unsigned w=0;w<16;w++) {
            W(AE350_ETH_BASE+0x78)=w;
            r[142+w]=ethernet_packet_reg(0x7c);
            if(w<8) {
                uint32_t er=(ethernet_packet_reg(0x74)>>24)&15u;
                er_pack|=(er<<(w*4));
            }
        }
        r[158]=er_pack;
        W(AE350_ETH_BASE+0x78)=0;
    }
    r[124]=ethernet_packet_reg(0x94);r[125]=ethernet_packet_reg(0x98);
    r[126]=ethernet_packet_reg(0x9c);r[127]=ethernet_packet_reg(0xa0);
    r[128]|=ethernet_packet_reg(0xa4);W(AE350_ETH_BASE+0xa4)=7;
}
static void dump_snapshot(unsigned phy) {
    sample_counters();
    uint16_t line_status=0;
    r[134]=(uint32_t)ethernet_mdio_read(phy,10,&line_status);
    r[132]=line_status;
    if(r[140]&2u) {
        unsigned cap_len=(r[140]>>16)&0x7ffu;
        unsigned words=(cap_len+3u)/4u;
        if(words>64u)words=64u;
        out("ETH BADFRAME DIAG ");hex(r[140]);out(" INFO ");hex(r[141]);out(" DATA");
        for(unsigned w=0;w<words;w++) {
            W(AE350_ETH_BASE+0x78)=w;
            uint32_t d=ethernet_packet_reg(0x7c);
            uint32_t er=(ethernet_packet_reg(0x74)>>24)&15u;
            out(" ");hex(d);out(":");hex1(er);
        }
        W(AE350_ETH_BASE+0x78)=0;
        out("\r\n");
    }
    out("ETH SNAP RX ");hex(r[6]);
    out(" TX ");hex(r[7]);
    out(" ARP ");hex(r[8]);
    out(" ICMP ");hex(r[9]);
    out(" UDP ");hex(r[10]);
    out(" CSUM ");hex(r[11]);
    out(" DROP ");hex(r[12]);
    out(" MDROP ");hex(r[121]);
    out(" DDROP ");hex(r[122]);
    out(" PRX ");hex(r[120]);
    out(" PTX ");hex(r[119]);
    out(" PRXER ");hex(r[135]);
    out(" DRXER ");hex(r[136]);
    out(" DFCS ");hex(r[137]);
    out(" MCRC ");hex(r[116]);
    out(" MPRE ");hex(r[117]);
    out(" DERR ");hex(ethernet_packet_reg(0x44)-base.error);
    out(" CTRL ");hex(r[138]);
    out(" DATA ");hex(r[139]);
    out(" DIAG ");hex(r[140]);
    out(" INFO ");hex(r[141]);
    out(" PHY10 ");hex(r[132]);
    out("\r\n");
}
static void process(const uint8_t *data,unsigned bytes) {
    unsigned kind=0;uint8_t *output=0;
    int acquired=ethernet_dma_tx_acquire(&output);
    if(acquired!=1) {r[114]++;return;}
    int n=ethernet_echo(&endpoint,data,bytes,output,1530,&kind);
    if(n>0) {
        if(ethernet_dma_tx_submit((unsigned)n))r[114]++;
        else {r[7]++;r[7+kind]++;}
    } else {
        ethernet_dma_tx_cancel();
        if(n<0)r[11]++;
    }
}
void main(void) {
    for(unsigned i=0;i<160;i++) {r[i]=0;}
    r[0]=0x45544801;out("ETH BEGIN\r\n");
    unsigned phy=0;uint32_t identity=0;
    int status=ethernet_phy_init(&phy,&identity);
    r[1]=25000000;r[3]=phy;r[4]=identity;r[5]=(uint32_t)status;
    if(status)goto done;
    r[2]=1;r[16+phy]=identity;
    out("ETH PHY ");hex(phy);out(" ID ");hex(identity);out("\r\n");
    if(ethernet_packet_reg(0x10)!=0xae350e03u) {r[5]=0xfffffffeu;goto done;}
    status=ethernet_phy_gigabit(phy);r[5]=(uint32_t)status;
    if(status)goto done;
    uint16_t bmsr=0;ethernet_mdio_read(phy,1,&bmsr);ethernet_mdio_read(phy,1,&bmsr);r[80+phy]=bmsr;
    uint16_t line_status=0;r[133]=(uint32_t)ethernet_mdio_read(phy,10,&line_status);r[131]=line_status;
    r[15]=ethernet_packet_reg(0x18);
    unsigned events=ethernet_packet_reg(0x1c);ethernet_delay_us(100000);
    r[112]=ethernet_packet_reg(0x1c)-events;r[113]=r[112]*1280u;
    if((r[15]&3)!=3||r[113]<120000000u||r[113]>130000000u) {r[5]=0xfffffffdu;goto done;}
    const struct ethernet_ring_config ring={
        .tx=(void *)0x02000000u,.rx=(void *)0x02001000u,
        .tx_buffers=(void *)0x02002000u,.rx_buffers=(void *)0x02004000u,
        .count=4,.stride=1536};
    if(ethernet_dma_init(&ring)) {r[5]=0xfffffffcu;goto done;}
    r[123]=0x444d4132u;
    reset_counters(phy);
    sample_counters();
    out("ETH PACKET READY\r\n");
    uint64_t start=ethernet_cycles();unsigned last=0,interactive=0;
    char cmd[64];unsigned cmd_len=0;
    while((ethernet_cycles()-start)/(AE350_CPU_HZ/1000u)<ETH_TEST_MS) {
        const uint8_t *data=0;unsigned bytes=0;
        int available=ethernet_dma_rx_acquire(&data,&bytes);
        if(available>0) {
            r[6]++;process(data,bytes);
            if(ethernet_dma_rx_release())r[115]++;
        }else if(available<0) {r[115]++;if(available==-4)r[14]++;break;}
        sample_counters();
        while(W(0xf0300034u)&1u) {
            char c=(char)(W(0xf0300020u)&0xffu);
            if(c=='\r'||c=='\n') {
                if(cmd_len>0) {
                    cmd[cmd_len]=0;cmd_len=0;interactive=1;start=ethernet_cycles();
                    if(cmd[0]=='Q')goto finish_packets;
                    else if(cmd[0]=='S')dump_snapshot(phy);
                    else if(cmd[0]=='R') {
                        if(ethernet_dma_stop())r[115]++;
                        ethernet_delay_us(200);
                        if(ethernet_dma_init(&ring))r[115]++;
                        reset_counters(phy);sample_counters();
                        out("ETH RESET CTRL ");hex(r[138]);out(" DATA ");hex(r[139]);out("\r\n");
                    } else if(cmd[0]=='T') {
                        uint32_t tap=0;
                        if(parse_hex(cmd+1,&tap)&&tap<=240u) {
                            if(ethernet_dma_stop())r[115]++;
                            ethernet_delay_us(200);
                            W(AE350_ETH_BASE+0x68)=0x80000000u|tap;
                            ethernet_delay_us(10);
                            if(ethernet_dma_init(&ring))r[115]++;
                            reset_counters(phy);sample_counters();
                            out("ETH TAP CTRL ");hex(r[138]);out(" DATA ");hex(r[139]);out("\r\n");
                        } else {
                            out("ETH TAP ERR\r\n");
                        }
                    } else if(cmd[0]=='L') {
                        uint32_t ctl=0,dpack=0;
                        const char *next=parse_hex(cmd+1,&ctl);
                        if(next&&parse_hex(next,&dpack)&&ctl<=240u&&
                           (dpack&0xffu)<=240u&&((dpack>>8)&0xffu)<=240u&&
                           ((dpack>>16)&0xffu)<=240u&&((dpack>>24)&0xffu)<=240u) {
                            if(ethernet_dma_stop())r[115]++;
                            ethernet_delay_us(200);
                            W(AE350_ETH_BASE+0x68)=ctl;
                            W(AE350_ETH_BASE+0x6c)=dpack;
                            ethernet_delay_us(10);
                            if(ethernet_dma_init(&ring))r[115]++;
                            reset_counters(phy);sample_counters();
                            out("ETH LANE CTRL ");hex(r[138]);out(" DATA ");hex(r[139]);out("\r\n");
                        } else {
                            out("ETH LANE ERR\r\n");
                        }
                    }
                }
            } else if(cmd_len+1<sizeof(cmd)) {
                cmd[cmd_len++]=c;
            }
        }
        if(!interactive) {
            unsigned sec=(unsigned)((ethernet_cycles()-start)/AE350_CPU_HZ);
            if(sec!=last) {last=sec;out("ETH COUNTS RX ");hex(r[6]);out(" TX ");hex(r[7]);out(" ARP ");hex(r[8]);out(" ICMP ");hex(r[9]);out(" UDP ");hex(r[10]);out("\r\n");}
        }
    }
 finish_packets:
    if(ethernet_dma_stop())r[115]++;
    sample_counters();
    uint64_t drain_start=ethernet_cycles();
    while(ethernet_packet_reg(0x58)-base.phy_tx<r[7] &&
          ethernet_cycles()-drain_start<AE350_CPU_HZ/10u) {}
    sample_counters();
    r[129]=ethernet_packet_reg(0x84);r[130]=ethernet_packet_reg(0xac);
    r[134]=(uint32_t)ethernet_mdio_read(phy,10,&line_status);r[132]=line_status;
    out("ETH PACKET DONE\r\n");
 done:
    r[159]=0x455448ff;
    out("ETH WORDS BEGIN\r\n");
    for(unsigned i=0;i<165;i++) {
        hex(r[i]); out((i%8==7||i==164) ? "\r\n" : " ");
    }
    out("ETH WORDS END\r\n");
    out("ETH DONE\r\n");for(;;)__asm__ volatile("wfi");
}
