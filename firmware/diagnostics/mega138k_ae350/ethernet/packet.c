/* Isolated-link DDR DMA ring ARP/ICMP/UDP echo diagnostic. */
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
    unsigned crc_start=ethernet_packet_reg(0x4c),preamble_start=ethernet_packet_reg(0x50);
    unsigned mac_drop_start=ethernet_packet_reg(0x5c);
    unsigned drop_start=ethernet_packet_reg(0x40),error_start=ethernet_packet_reg(0x44);
    unsigned phy_rx_start=ethernet_packet_reg(0x54),phy_tx_start=ethernet_packet_reg(0x58);
    out("ETH PACKET READY\r\n");
    uint64_t start=ethernet_cycles();unsigned last=0;
    while((ethernet_cycles()-start)/(AE350_CPU_HZ/1000u)<ETH_TEST_MS) {
        const uint8_t *data=0;unsigned bytes=0;
        int available=ethernet_dma_rx_acquire(&data,&bytes);
        if(available>0) {
            r[6]++;process(data,bytes);
            if(ethernet_dma_rx_release())r[115]++;
        }else if(available<0) {r[115]++;if(available==-4)r[14]++;break;}
        r[121]=ethernet_packet_reg(0x5c)-mac_drop_start;r[122]=ethernet_packet_reg(0x40)-drop_start;
        r[12]=r[121]+r[122];
        r[116]=ethernet_packet_reg(0x4c)-crc_start;r[117]=ethernet_packet_reg(0x50)-preamble_start;
        r[13]=ethernet_packet_reg(0x44)-error_start+r[116]+r[117];
        r[118]=ethernet_packet_reg(0x38);r[119]=ethernet_packet_reg(0x58)-phy_tx_start;r[120]=ethernet_packet_reg(0x54)-phy_rx_start;
        r[124]=ethernet_packet_reg(0x94);r[125]=ethernet_packet_reg(0x98);
        r[126]=ethernet_packet_reg(0x9c);r[127]=ethernet_packet_reg(0xa0);
        r[128]|=ethernet_packet_reg(0xa4);W(AE350_ETH_BASE+0xa4)=7;
        unsigned sec=(unsigned)((ethernet_cycles()-start)/AE350_CPU_HZ);
        if(sec!=last) {last=sec;out("ETH COUNTS RX ");hex(r[6]);out(" TX ");hex(r[7]);out(" ARP ");hex(r[8]);out(" ICMP ");hex(r[9]);out(" UDP ");hex(r[10]);out("\r\n");}
    }
    if(ethernet_dma_stop())r[115]++;
    r[124]=ethernet_packet_reg(0x94);r[125]=ethernet_packet_reg(0x98);
    r[126]=ethernet_packet_reg(0x9c);r[127]=ethernet_packet_reg(0xa0);
    r[128]|=ethernet_packet_reg(0xa4);
    uint64_t drain_start=ethernet_cycles();
    while(ethernet_packet_reg(0x58)-phy_tx_start<r[7] &&
          ethernet_cycles()-drain_start<AE350_CPU_HZ/10u) {}
    r[118]=ethernet_packet_reg(0x38);r[119]=ethernet_packet_reg(0x58)-phy_tx_start;
    r[129]=ethernet_packet_reg(0x84);r[130]=ethernet_packet_reg(0xac);
    r[134]=(uint32_t)ethernet_mdio_read(phy,10,&line_status);r[132]=line_status;
    out("ETH PACKET DONE\r\n");
 done:
    r[159]=0x455448ff;out("ETH DONE\r\n");for(;;)__asm__ volatile("wfi");
}
